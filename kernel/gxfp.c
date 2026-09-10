// SPDX-License-Identifier: GPL-2.0
/*
 * gxfp - Goodix GXFP51A0 "Milan-SPI" fingerprint sensor
 *
 * Huawei MateBook 13 (2020, WRTB-WXX9). The sensor is an ACPI-enumerated SPI
 * device on the PCH On-Package SPI #1 (PCI 8086:02ab -> pxa2xx-spi -> spi1).
 *
 * _CRS gives: SpiSerialBusV2(cs 0, PolarityLow, FourWireMode, 8 bit,
 *             ControllerInitiated, 10 MHz, CPOL=0, CPHA=0)
 *             GpioInt(Level, ActiveHigh)  -> sensor interrupt
 *             GpioIo (OutputOnly, PullUp) -> sensor enable / reset
 *
 * Wire protocol recovered from the Windows UMDF driver gfspi.dll v1.1.141.40;
 * see docs/PROTOCOL.md.
 *
 * This revision is a bring-up/diagnostic driver: it claims the ACPI resources
 * properly and exposes debugfs knobs so the protocol can be exercised from
 * userspace without rebuilding.
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/delay.h>
#include <linux/acpi.h>
#include <linux/spi/spi.h>
#include <linux/gpio/consumer.h>
#include <linux/interrupt.h>
#include <linux/debugfs.h>
#include <linux/mutex.h>
#include <linux/wait.h>
#include <linux/seq_file.h>
#include <linux/io.h>
#include <linux/moduleparam.h>

#define GXFP_NAME	"gxfp"
#define GXFP_BUF_MAX	4096

/* SPI transport header, both directions (MakeSpiHeader/CheckPackage in gfspi.dll) */
#define GXFP_PKT_MSG	0xA
#define GXFP_PKT_TLS	0xB
#define GXFP_NO_CKSUM	0x88

struct gxfp {
	struct spi_device	*spi;
	struct gpio_desc	*enable;
	struct gpio_desc	*irq_gpio;
	int			irq;

	struct mutex		lock;
	wait_queue_head_t	wq;
	atomic_t		irq_count;
	bool			data_ready;
	bool			irq_masked;

	struct dentry		*dbg;
	u8			*txbuf;
	u8			*rxbuf;
	size_t			rxlen;
};

/* ---------------------------------------------------------------- protocol */

static void gxfp_make_spi_hdr(u8 *h, u16 body_len, u8 type)
{
	h[0] = (type << 4) & 0xF0;
	h[1] = body_len & 0xFF;
	h[2] = (body_len >> 8) & 0xFF;
	h[3] = (u8)(h[0] + h[2] + h[1]);
}

static int gxfp_check_spi_hdr(const u8 *h)
{
	u8 type = h[0] >> 4;

	if (type != GXFP_PKT_MSG && type != GXFP_PKT_TLS)
		return -EPROTO;
	if (h[3] != (u8)(h[0] + h[2] + h[1]))
		return -EBADMSG;
	return h[1] | (h[2] << 8);
}

/*
 * PeripheralWriteWrapper() @0x180008130: the 4-byte header goes out in its own
 * chip-select, then a 2 ms gap, then the body in a second chip-select.
 */
static int gxfp_send(struct gxfp *g, u8 cmd0, u8 cmd1,
		     const u8 *payload, u16 n, bool checksum)
{
	u8 hdr[4], *body;
	u16 blen = n + 4;
	u8 cmd = (cmd0 << 4) | (cmd1 << 1);
	u8 lo = (n + 1) & 0xFF, hi = ((n + 1) >> 8) & 0xFF;
	int ret, i;

	if (blen > GXFP_BUF_MAX)
		return -EINVAL;

	body = kzalloc(blen, GFP_KERNEL);
	if (!body)
		return -ENOMEM;

	body[0] = cmd;
	body[1] = lo;
	body[2] = hi;
	if (n)
		memcpy(body + 3, payload, n);
	if (checksum) {
		u8 s = cmd + lo + hi;

		for (i = 0; i < n; i++)
			s += payload[i];
		body[3 + n] = (u8)(0xAA - s);
	} else {
		body[3 + n] = GXFP_NO_CKSUM;
	}

	gxfp_make_spi_hdr(hdr, blen, GXFP_PKT_MSG);

	dev_info(&g->spi->dev, "tx cmd=0x%02x hdr=%4ph body[%u]=%*ph\n",
		 cmd, hdr, blen, min_t(int, blen, 16), body);

	ret = spi_write(g->spi, hdr, 4);
	if (ret) {
		dev_err(&g->spi->dev, "header write failed: %d\n", ret);
		goto out;
	}
	usleep_range(2000, 2500);		/* the driver's Sleep(2) */
	ret = spi_write(g->spi, body, blen);
	if (ret)
		dev_err(&g->spi->dev, "body write failed: %d\n", ret);
out:
	kfree(body);
	return ret;
}

/* MilanEvtInterruptIsr() @0x1800119a0: read 4-byte header, then that many bytes */
static int gxfp_recv(struct gxfp *g)
{
	u8 hdr[4];
	int n, ret;

	ret = spi_read(g->spi, hdr, 4);
	if (ret)
		return ret;

	n = gxfp_check_spi_hdr(hdr);
	dev_info(&g->spi->dev, "rx hdr=%4ph -> %d\n", hdr, n);
	if (n < 0)
		return n;
	if (n == 0 || n > GXFP_BUF_MAX)
		return -EMSGSIZE;

	ret = spi_read(g->spi, g->rxbuf, n);
	if (ret)
		return ret;
	g->rxlen = n;
	dev_info(&g->spi->dev, "rx body[%d]=%*ph\n", n, min(n, 32), g->rxbuf);
	return n;
}

#define GXFP_IRQ_STORM	200

static irqreturn_t gxfp_isr(int irq, void *dev_id)
{
	struct gxfp *g = dev_id;
	int n = atomic_inc_return(&g->irq_count);

	g->data_ready = true;
	wake_up_interruptible(&g->wq);

	/*
	 * The GpioInt is declared level/active-high. On this board the line sits
	 * asserted whenever the enable GPIO is high, so an unserviced level IRQ
	 * free-runs. Mask it rather than burn a core; userspace can re-arm via
	 * debugfs once we know how to make the sensor deassert.
	 */
	if (n == GXFP_IRQ_STORM && !g->irq_masked) {
		g->irq_masked = true;
		disable_irq_nosync(irq);
		dev_warn(&g->spi->dev,
			 "IRQ storm (%d) - line stuck asserted, masking irq %d\n",
			 n, irq);
	}
	return IRQ_HANDLED;
}

/* ------------------------------------------- Intel LPSS SSP controller regs
 * BAR0 of PCI 00:1e.3 (8086:02ab). pxa2xx-spi owns it; we only ioremap and read.
 * Register map (drivers/spi/spi-pxa2xx.c, LPSS_CNL_SSP):
 *   0x00 SSCR0  0x04 SSCR1  0x08 SSSR  0x0c SSITR  0x10 SSDR
 *   private block at 0x200: +0x20 SSP_REG, +0x24 CS_CONTROL, +0xfc CAPABILITIES
 * CS_CONTROL: bit0 SW_MODE, bit1 CS_HIGH(deasserted), bits 9:8 cs_sel
 */
static int touch_enable = 1;
module_param(touch_enable, int, 0444);
MODULE_PARM_DESC(touch_enable, "1/0 = drive the enable GPIO high/low at probe, -1 = leave it alone");

/* Chip-select timing. The LPSS SSP has no CS setup/hold register; the SPI core
 * applies these delays generically in spi_set_cs(). Windows' SPB stack may insert
 * its own, so make them tunable to test that hypothesis. Values in microseconds. */
static int cs_setup_us, cs_hold_us, cs_inactive_us;
module_param(cs_setup_us, int, 0444);
module_param(cs_hold_us, int, 0444);
module_param(cs_inactive_us, int, 0444);

/* Intel GPIO community 0 (GPP_A/B/G). COM0 = SBRG + 0x006E0000; on CNL/CML-LP
 * the P2SB SBRG is 0xFD000000. GPP_B pad table starts at community offset 0x790,
 * 16 bytes per pad. GSPI1 pads are pin 44..47 = GPP_B[19..22]:
 *      CS0B 0x8C0, CLK 0x8D0, MISO 0x8E0, MOSI 0x8F0
 * Read-only sampler: are these PADCFG0 registers live, and does MISO ever move?
 */
static unsigned long gpio_com0 = 0xFD6E0000;
module_param(gpio_com0, ulong, 0444);

static unsigned long lpss_phys = 0x9b804000;
module_param(lpss_phys, ulong, 0444);
MODULE_PARM_DESC(lpss_phys, "physical address of the LPSS SSP BAR0");

static int gxfp_ctlregs_show(struct seq_file *sf, void *unused)
{
	void __iomem *base = ioremap(lpss_phys, 0x1000);
	u32 sscr0, sscr1, sssr, cs, ssp, caps;

	if (!base) {
		seq_puts(sf, "ioremap failed\n");
		return 0;
	}
	sscr0 = readl(base + 0x00);
	sscr1 = readl(base + 0x04);
	sssr  = readl(base + 0x08);
	ssp   = readl(base + 0x220);
	cs    = readl(base + 0x224);
	caps  = readl(base + 0x2fc);

	seq_printf(sf, "BAR0        0x%lx\n", lpss_phys);
	seq_printf(sf, "SSCR0       0x%08x  SSE=%d DSS=%d SCR=%d\n",
		   sscr0, !!(sscr0 & (1 << 7)), (sscr0 & 0xf) + 1,
		   (sscr0 >> 8) & 0xfff);
	seq_printf(sf, "SSCR1       0x%08x  SPO(CPOL)=%d SPH(CPHA)=%d\n",
		   sscr1, !!(sscr1 & (1 << 3)), !!(sscr1 & (1 << 4)));
	seq_printf(sf, "SSSR        0x%08x\n", sssr);
	seq_printf(sf, "SSP_REG     0x%08x\n", ssp);
	seq_printf(sf, "CS_CONTROL  0x%08x  SW_MODE=%d CS_HIGH(deasserted)=%d cs_sel=%d\n",
		   cs, !!(cs & 1), !!(cs & 2), (cs >> 8) & 3);
	seq_printf(sf, "CAPS        0x%08x\n", caps);
	iounmap(base);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(gxfp_ctlregs);

/*
 * Drive a long, slow transfer in the background and sample CS_CONTROL while it
 * runs, so we can see whether the chip select actually asserts on the wire.
 */
static int gxfp_cstest_show(struct seq_file *sf, void *unused)
{
	struct gxfp *g = sf->private;
	void __iomem *base = ioremap(lpss_phys, 0x1000);
	struct spi_transfer t = { .tx_buf = g->txbuf, .rx_buf = g->rxbuf,
				  .len = 2048, .speed_hz = 100000 };
	struct spi_message m;
	u32 samples[24], s0[24], s1[24], ss[24];
	int i, ret;

	if (!base) {
		seq_puts(sf, "ioremap failed\n");
		return 0;
	}
	memset(g->txbuf, 0x00, 2048);
	spi_message_init(&m);
	spi_message_add_tail(&t, &m);

	seq_printf(sf, "CS_CONTROL before: 0x%08x\n", readl(base + 0x224));

	/* spi_async returns immediately; the message runs on the controller thread */
	m.complete = NULL;
	m.context = NULL;
	ret = spi_async(g->spi, &m);
	for (i = 0; i < ARRAY_SIZE(samples); i++) {
		s0[i]      = readl(base + 0x00);
		s1[i]      = readl(base + 0x04);
		ss[i]      = readl(base + 0x08);
		samples[i] = readl(base + 0x224);
		udelay(200);
	}
	seq_printf(sf, "spi_async ret %d\n", ret);
	seq_puts(sf, "CS_CONTROL samples during a 2048-byte @100 kHz transfer:\n ");
	for (i = 0; i < ARRAY_SIZE(samples); i++)
		seq_printf(sf, " %08x", samples[i]);
	seq_puts(sf, "\n");

	/* Decode the SSP config captured while the transfer was actually running. */
	for (i = 0; i < ARRAY_SIZE(s0); i++) {
		if (!(s0[i] & (1 << 7)))          /* SSE clear -> not running yet */
			continue;
		seq_printf(sf,
			"LIVE sample %d: SSCR0=0x%08x SSCR1=0x%08x SSSR=0x%08x\n",
			i, s0[i], s1[i], ss[i]);
		seq_printf(sf,
			"   SSE=%d  DSS(bits)=%u  FRF=%u(%s)  SCR=%u  EDSS=%d\n",
			!!(s0[i] & (1 << 7)),
			(s0[i] & 0xf) + 1 + (((s0[i] >> 20) & 1) ? 16 : 0),
			(s0[i] >> 4) & 3,
			((s0[i] >> 4) & 3) == 0 ? "Motorola SPI" :
			((s0[i] >> 4) & 3) == 1 ? "TI SSP" :
			((s0[i] >> 4) & 3) == 2 ? "Microwire" : "PSP",
			(s0[i] >> 8) & 0xfff, !!(s0[i] & (1 << 20)));
		seq_printf(sf,
			"   SPO(CPOL)=%d SPH(CPHA)=%d  -> SPI mode %d\n",
			!!(s1[i] & (1 << 3)), !!(s1[i] & (1 << 4)),
			(!!(s1[i] & (1 << 3)) << 1) | !!(s1[i] & (1 << 4)));
		break;
	}
	msleep(400);
	seq_printf(sf, "CS_CONTROL after:  0x%08x\n", readl(base + 0x224));
	seq_printf(sf, "rx first 16: %16ph\n", g->rxbuf);
	iounmap(base);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(gxfp_cstest);

/* ------------------------------------------------------- ACPI _DSM transport
 * The DSDT device \_SB.PCI0.SPI1.SPBA carries
 *   OperationRegion (HWFP, SystemMemory, FPAD, 0x0800)
 *   Method (_DSM, ...) UUID cc58b68a-4479-4893-a8bb-961209db59e5, rev 0
 *       func 0 -> Buffer(1){0x03}   (functions 0 and 1 supported)
 *       func 1 -> the whole 2048-byte HWFP window
 * gfspi.dll drives this from peripheral.c ("EvaluateASLMethod", "before DSM:",
 * "SUCCESS copied the returned data of ACPI method", "ACPI_HWFP firmware table
 * not found"). /dev/mem cannot reach the window (CONFIG_STRICT_DEVMEM) so we
 * read it here instead.
 */
static const guid_t gxfp_dsm_guid =
	GUID_INIT(0xcc58b68a, 0x4479, 0x4893,
		  0xa8, 0xbb, 0x96, 0x12, 0x09, 0xdb, 0x59, 0xe5);

static int gxfp_dsm_show(struct seq_file *sf, void *unused)
{
	struct gxfp *g = sf->private;
	acpi_handle h = ACPI_HANDLE(&g->spi->dev);
	union acpi_object *obj;
	int i;

	if (!h) {
		seq_puts(sf, "no ACPI handle\n");
		return 0;
	}

	for (i = 0; i <= 1; i++) {
		seq_printf(sf, "_DSM func %d: ", i);
		obj = acpi_evaluate_dsm(h, &gxfp_dsm_guid, 0, i, NULL);
		if (!obj) {
			seq_puts(sf, "returned NULL\n");
			continue;
		}
		if (obj->type != ACPI_TYPE_BUFFER) {
			seq_printf(sf, "type %d (not buffer)\n", obj->type);
		} else {
			int n = obj->buffer.length, j, nz = 0;

			for (j = 0; j < n; j++)
				if (obj->buffer.pointer[j])
					nz++;
			seq_printf(sf, "%d bytes, %d non-zero\n", n, nz);
			for (j = 0; j < n; j += 16) {
				seq_printf(sf, "  %04x  %*ph\n", j,
					   min(16, n - j), obj->buffer.pointer + j);
				if (j >= 512 && !nz)
					break;
			}
		}
		ACPI_FREE(obj);
	}
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(gxfp_dsm);

/* Replay the exact Windows opening sequence (PROTOCOL.md 10.2) and report. */
static int gxfp_replay_show(struct seq_file *sf, void *unused)
{
	struct gxfp *g = sf->private;
	static const struct { u8 c0, c1, n; const char *name; } seq[] = {
		{ 0x0, 0, 4, "NOP" },
		{ 0x9, 3, 2, "0x96 DriverState:Install" },
		{ 0x0, 0, 4, "NOP" },
		{ 0xA, 4, 2, "0xa8 GetEvkVersion" },
		{ 0xA, 1, 2, "0xa2 soft reset" },
		{ 0x8, 1, 5, "0x82 ChipRegRead" },
	};
	u8 payload[8] = { 0 };
	u8 hdr[4];
	int i, n, ret;

	seq_printf(sf, "cs_setup=%dus cs_hold=%dus cs_inactive=%dus\n",
		   cs_setup_us, cs_hold_us, cs_inactive_us);
	mutex_lock(&g->lock);
	for (i = 0; i < ARRAY_SIZE(seq); i++) {
		ret = gxfp_send(g, seq[i].c0, seq[i].c1, payload, seq[i].n, true);
		if (ret) {
			seq_printf(sf, "  %-26s send failed %d\n", seq[i].name, ret);
			continue;
		}
		msleep(50);
		if (spi_read(g->spi, hdr, 4)) {
			seq_printf(sf, "  %-26s read failed\n", seq[i].name);
			continue;
		}
		n = gxfp_check_spi_hdr(hdr);
		seq_printf(sf, "  %-26s hdr=%4ph -> %s\n", seq[i].name, hdr,
			   n < 0 ? "no reply" : "REPLY!");
		if (n > 0 && n <= GXFP_BUF_MAX && !spi_read(g->spi, g->rxbuf, n))
			seq_printf(sf, "      body[%d]: %*ph\n", n,
				   min(n, 32), g->rxbuf);
	}
	mutex_unlock(&g->lock);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(gxfp_replay);

#define PADOFF_CS0B 0x8C0
#define PADOFF_CLK  0x8D0
#define PADOFF_MISO 0x8E0
#define PADOFF_MOSI 0x8F0

static int gxfp_padprobe_show(struct seq_file *sf, void *unused)
{
	struct gxfp *g = sf->private;
	void __iomem *com = ioremap(gpio_com0, 0x1000);
	static const struct { u32 off; const char *n; u32 expect; } pads[] = {
		{ PADOFF_CS0B, "CS0B", 0x44000700 },
		{ PADOFF_CLK,  "CLK ", 0x44000700 },
		{ PADOFF_MISO, "MISO", 0x44000702 },
		{ PADOFF_MOSI, "MOSI", 0x44000700 },
	};
	u32 seen_set[4] = {0}, seen_clr[4] = {0}, v;
	struct spi_transfer t = { .tx_buf = g->txbuf, .rx_buf = g->rxbuf,
				  .len = 2048, .speed_hz = 50000 };
	struct spi_message m;
	int i, k, ok = 1;
	unsigned long n = 0;

	if (!com) { seq_puts(sf, "ioremap failed\n"); return 0; }

	seq_printf(sf, "GPIO community 0 @ 0x%lx\n", gpio_com0);
	for (i = 0; i < ARRAY_SIZE(pads); i++) {
		v = readl(com + pads[i].off);
		seq_printf(sf, "  %s pad @+0x%03x = 0x%08x  (pinctrl says 0x%08x) %s\n",
			   pads[i].n, pads[i].off, v, pads[i].expect,
			   v == pads[i].expect ? "MATCH" : "*** MISMATCH ***");
		if (v != pads[i].expect) ok = 0;
	}
	if (!ok) {
		seq_puts(sf, "\nAddress does not match pinctrl - aborting, not sampling.\n");
		iounmap(com);
		return 0;
	}

	memset(g->txbuf, 0x00, 2048);
	spi_message_init(&m);
	spi_message_add_tail(&t, &m);
	seq_puts(sf, "\nSampling all 4 pads during a 2048-byte @50 kHz transfer...\n");
	spi_async(g->spi, &m);
	for (k = 0; k < 400000; k++) {
		for (i = 0; i < ARRAY_SIZE(pads); i++) {
			v = readl(com + pads[i].off);
			seen_set[i] |= v;
			seen_clr[i] |= ~v;
		}
		n++;
	}
	msleep(600);
	seq_printf(sf, "%lu samples of each pad\n", n);
	for (i = 0; i < ARRAY_SIZE(pads); i++) {
		u32 changing = seen_set[i] & seen_clr[i];
		seq_printf(sf, "  %s: bits that CHANGED = 0x%08x  %s",
			   pads[i].n, changing,
			   changing ? "<<< LIVE" : "(frozen)");
		if (changing & (1 << 1)) seq_puts(sf, "  [RXSTATE toggled!]");
		seq_puts(sf, "\n");
	}
	/*
	 * The pads are [LOCKED full] (PADCFGLOCK set), so the hardware should
	 * ignore writes. Try anyway: clearing GPIORXDIS (bit 9) would make
	 * GPIORXSTATE (bit 1) follow the real pin, turning these registers into
	 * a genuine logic probe on CLK / MOSI / MISO. Read back to see if it
	 * took, and restore the original value unconditionally afterwards.
	 */
	seq_puts(sf, "\nAttempting to clear GPIORXDIS (bit 9) to unfreeze RXSTATE:\n");
	{
		u32 orig[4], after[4];
		int took = 0;

		for (i = 0; i < ARRAY_SIZE(pads); i++) {
			orig[i] = readl(com + pads[i].off);
			writel(orig[i] & ~(1 << 9), com + pads[i].off);
			after[i] = readl(com + pads[i].off);
			seq_printf(sf, "  %s 0x%08x -> wrote 0x%08x -> reads 0x%08x  %s\n",
				   pads[i].n, orig[i], orig[i] & ~(1u << 9), after[i],
				   (after[i] & (1 << 9)) ? "BLOCKED (locked)" : "TOOK");
			if (!(after[i] & (1 << 9))) took = 1;
		}
		if (took) {
			memset(seen_set, 0, sizeof(seen_set));
			memset(seen_clr, 0, sizeof(seen_clr));
			spi_message_init(&m);
			spi_message_add_tail(&t, &m);
			spi_async(g->spi, &m);
			for (k = 0; k < 400000; k++)
				for (i = 0; i < ARRAY_SIZE(pads); i++) {
					v = readl(com + pads[i].off);
					seen_set[i] |= v;
					seen_clr[i] |= ~v;
				}
			msleep(600);
			seq_puts(sf, "  re-sampled with RX enabled:\n");
			for (i = 0; i < ARRAY_SIZE(pads); i++)
				seq_printf(sf, "    %s changed=0x%08x %s\n", pads[i].n,
					   seen_set[i] & seen_clr[i],
					   (seen_set[i] & seen_clr[i] & 2) ?
					   "<<< PIN IS TOGGLING" : "(static)");
		}
		for (i = 0; i < ARRAY_SIZE(pads); i++)
			writel(orig[i], com + pads[i].off);
		seq_puts(sf, "  original pad values restored.\n");
	}

	iounmap(com);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(gxfp_padprobe);

/* ---------------------------------------------------------------- debugfs */

static int gxfp_enable_get(void *data, u64 *val)
{
	struct gxfp *g = data;

	*val = gpiod_get_value_cansleep(g->enable);
	return 0;
}

static int gxfp_enable_set(void *data, u64 val)
{
	struct gxfp *g = data;

	gpiod_set_value_cansleep(g->enable, !!val);
	return 0;
}
DEFINE_DEBUGFS_ATTRIBUTE(gxfp_enable_fops, gxfp_enable_get, gxfp_enable_set, "%llu\n");

static int gxfp_irqcount_get(void *data, u64 *val)
{
	*val = atomic_read(&((struct gxfp *)data)->irq_count);
	return 0;
}
DEFINE_DEBUGFS_ATTRIBUTE(gxfp_irqcount_fops, gxfp_irqcount_get, NULL, "%llu\n");

static int gxfp_irqlevel_get(void *data, u64 *val)
{
	struct gxfp *g = data;

	*val = g->irq_gpio ? gpiod_get_value_cansleep(g->irq_gpio) : 0xFF;
	return 0;
}
DEFINE_DEBUGFS_ATTRIBUTE(gxfp_irqlevel_fops, gxfp_irqlevel_get, NULL, "%llu\n");

static int gxfp_reset_set(void *data, u64 val)
{
	struct gxfp *g = data;
	unsigned int keep = val ? (unsigned int)val : 10;

	gpiod_set_value_cansleep(g->enable, 0);
	msleep(keep);
	gpiod_set_value_cansleep(g->enable, 1);
	msleep(100);
	dev_info(&g->spi->dev, "reset pulse %u ms, irq level now %d\n",
		 keep, g->irq_gpio ? gpiod_get_value_cansleep(g->irq_gpio) : -1);
	return 0;
}
DEFINE_DEBUGFS_ATTRIBUTE(gxfp_reset_fops, NULL, gxfp_reset_set, "%llu\n");

/* write "cmd0 cmd1 [hex payload bytes]" e.g. "9 0 00 00 00 00" */
static ssize_t gxfp_cmd_write(struct file *f, const char __user *ubuf,
			      size_t len, loff_t *off)
{
	struct gxfp *g = file_inode(f)->i_private;
	char *buf, *p, *tok;
	u8 payload[256];
	unsigned int cmd0, cmd1, n = 0, v;
	int ret;

	if (len > 1024)
		return -EINVAL;
	buf = memdup_user_nul(ubuf, len);
	if (IS_ERR(buf))
		return PTR_ERR(buf);

	p = strim(buf);
	tok = strsep(&p, " \t");
	if (!tok || kstrtouint(tok, 16, &cmd0)) { ret = -EINVAL; goto out; }
	tok = strsep(&p, " \t");
	if (!tok || kstrtouint(tok, 16, &cmd1)) { ret = -EINVAL; goto out; }
	while (p && n < sizeof(payload)) {
		tok = strsep(&p, " \t");
		if (!tok || !*tok)
			continue;
		if (kstrtouint(tok, 16, &v)) { ret = -EINVAL; goto out; }
		payload[n++] = v & 0xFF;
	}

	mutex_lock(&g->lock);
	g->data_ready = false;
	ret = gxfp_send(g, cmd0 & 0xF, cmd1 & 0x7, payload, n, true);
	if (!ret) {
		wait_event_interruptible_timeout(g->wq, g->data_ready,
						 msecs_to_jiffies(500));
		gxfp_recv(g);
	}
	mutex_unlock(&g->lock);
out:
	kfree(buf);
	return ret ? ret : len;
}

static const struct file_operations gxfp_cmd_fops = {
	.owner = THIS_MODULE,
	.open  = simple_open,
	.write = gxfp_cmd_write,
	.llseek = noop_llseek,
};

/* raw duplex: write hex bytes, read back the same number of bytes */
static ssize_t gxfp_raw_write(struct file *f, const char __user *ubuf,
			      size_t len, loff_t *off)
{
	struct gxfp *g = file_inode(f)->i_private;
	char *buf, *p, *tok;
	unsigned int n = 0, v;
	int ret;

	if (len > 4096)
		return -EINVAL;
	buf = memdup_user_nul(ubuf, len);
	if (IS_ERR(buf))
		return PTR_ERR(buf);

	p = strim(buf);
	while (p && n < GXFP_BUF_MAX) {
		tok = strsep(&p, " \t");
		if (!tok || !*tok)
			continue;
		if (kstrtouint(tok, 16, &v)) { kfree(buf); return -EINVAL; }
		g->txbuf[n++] = v & 0xFF;
	}
	kfree(buf);
	if (!n)
		return -EINVAL;

	mutex_lock(&g->lock);
	{
		struct spi_transfer t = {
			.tx_buf = g->txbuf, .rx_buf = g->rxbuf, .len = n,
		};
		struct spi_message m;

		spi_message_init(&m);
		spi_message_add_tail(&t, &m);
		ret = spi_sync(g->spi, &m);
	}
	g->rxlen = ret ? 0 : n;
	mutex_unlock(&g->lock);
	dev_info(&g->spi->dev, "raw %u bytes ret=%d rx=%*ph\n",
		 n, ret, min_t(int, n, 32), g->rxbuf);
	return ret ? ret : len;
}

static ssize_t gxfp_raw_read(struct file *f, char __user *ubuf,
			     size_t len, loff_t *off)
{
	struct gxfp *g = file_inode(f)->i_private;
	char *out;
	size_t i, n;
	ssize_t ret;

	mutex_lock(&g->lock);
	n = g->rxlen;
	out = kmalloc(n * 3 + 2, GFP_KERNEL);
	if (!out) { mutex_unlock(&g->lock); return -ENOMEM; }
	for (i = 0; i < n; i++)
		sprintf(out + i * 3, "%02x ", g->rxbuf[i]);
	out[n * 3] = '\n';
	mutex_unlock(&g->lock);
	ret = simple_read_from_buffer(ubuf, len, off, out, n * 3 + 1);
	kfree(out);
	return ret;
}

static const struct file_operations gxfp_raw_fops = {
	.owner = THIS_MODULE,
	.open  = simple_open,
	.write = gxfp_raw_write,
	.read  = gxfp_raw_read,
	.llseek = default_llseek,
};

/* ---------------------------------------------------------------- probe */

static int gxfp_probe(struct spi_device *spi)
{
	struct device *dev = &spi->dev;
	struct acpi_device *adev = ACPI_COMPANION(dev);
	struct gxfp *g;
	int ret, i;

	g = devm_kzalloc(dev, sizeof(*g), GFP_KERNEL);
	if (!g)
		return -ENOMEM;
	g->spi = spi;
	g->txbuf = devm_kzalloc(dev, GXFP_BUF_MAX, GFP_KERNEL);
	g->rxbuf = devm_kzalloc(dev, GXFP_BUF_MAX, GFP_KERNEL);
	if (!g->txbuf || !g->rxbuf)
		return -ENOMEM;
	mutex_init(&g->lock);
	init_waitqueue_head(&g->wq);
	spi_set_drvdata(spi, g);

	spi->bits_per_word = 8;
	spi->mode = SPI_MODE_0;
	if (cs_setup_us) {
		spi->cs_setup.value = cs_setup_us;
		spi->cs_setup.unit  = SPI_DELAY_UNIT_USECS;
	}
	if (cs_hold_us) {
		spi->cs_hold.value = cs_hold_us;
		spi->cs_hold.unit  = SPI_DELAY_UNIT_USECS;
	}
	if (cs_inactive_us) {
		spi->cs_inactive.value = cs_inactive_us;
		spi->cs_inactive.unit  = SPI_DELAY_UNIT_USECS;
	}
	ret = spi_setup(spi);
	if (ret)
		dev_warn(dev, "spi_setup: %d\n", ret);

	dev_info(dev, "cs_setup=%dus cs_hold=%dus cs_inactive=%dus\n",
		 cs_setup_us, cs_hold_us, cs_inactive_us);
	dev_info(dev, "ACPI %s: cs=%u mode=0x%x bits=%u max=%u Hz\n",
		 adev ? acpi_dev_name(adev) : "?",
		 spi_get_chipselect(spi, 0), spi->mode,
		 spi->bits_per_word, spi->max_speed_hz);

	/*
	 * _CRS resource order is SpiSerialBus, GpioInt, GpioIo. The ACPI GPIO
	 * helper indexes GpioInt and GpioIo together, so the output we want is
	 * normally index 1 - but probe both and report what we find.
	 */
	for (i = 0; i < 2; i++) {
		struct gpio_desc *d = devm_gpiod_get_index_optional(dev, NULL, i,
								   GPIOD_ASIS);
		if (IS_ERR(d)) {
			dev_info(dev, "gpiod idx %d: err %ld\n", i, PTR_ERR(d));
			continue;
		}
		if (!d) {
			dev_info(dev, "gpiod idx %d: absent\n", i);
			continue;
		}
		dev_info(dev, "gpiod idx %d: dir=%d value=%d\n", i,
			 gpiod_get_direction(d), gpiod_get_value_cansleep(d));
		if (gpiod_get_direction(d) == 0 && !g->enable)	/* 0 = output */
			g->enable = d;
		else if (!g->irq_gpio)
			g->irq_gpio = d;
	}
	if (!g->enable) {
		dev_err(dev, "no output (enable) GPIO found in _CRS\n");
		return -ENODEV;
	}
	if (touch_enable >= 0)
		gpiod_direction_output(g->enable, !!touch_enable);
	else
		dev_info(dev, "leaving enable GPIO untouched (touch_enable=-1)\n");

	g->irq = adev ? acpi_dev_gpio_irq_get(adev, 0) : -ENODEV;
	if (g->irq < 0) {
		dev_warn(dev, "no ACPI GpioInt: %d\n", g->irq);
	} else {
		ret = devm_request_threaded_irq(dev, g->irq, NULL, gxfp_isr,
						IRQF_ONESHOT, GXFP_NAME, g);
		if (ret) {
			dev_warn(dev, "request_irq(%d): %d\n", g->irq, ret);
			g->irq = -1;
		} else {
			dev_info(dev, "irq %d requested\n", g->irq);
		}
	}

	g->dbg = debugfs_create_dir(GXFP_NAME, NULL);
	debugfs_create_file("enable",   0644, g->dbg, g, &gxfp_enable_fops);
	debugfs_create_file("reset",    0200, g->dbg, g, &gxfp_reset_fops);
	debugfs_create_file("irq_count",0444, g->dbg, g, &gxfp_irqcount_fops);
	debugfs_create_file("irq_level",0444, g->dbg, g, &gxfp_irqlevel_fops);
	debugfs_create_file("cmd",      0200, g->dbg, g, &gxfp_cmd_fops);
	debugfs_create_file("raw",      0644, g->dbg, g, &gxfp_raw_fops);
	debugfs_create_file("dsm",      0400, g->dbg, g, &gxfp_dsm_fops);
	debugfs_create_file("ctlregs",  0400, g->dbg, g, &gxfp_ctlregs_fops);
	debugfs_create_file("cstest",   0400, g->dbg, g, &gxfp_cstest_fops);
	debugfs_create_file("replay",   0400, g->dbg, g, &gxfp_replay_fops);
	debugfs_create_file("padprobe", 0400, g->dbg, g, &gxfp_padprobe_fops);

	dev_info(dev, "probed; debugfs at /sys/kernel/debug/" GXFP_NAME "\n");
	return 0;
}

static void gxfp_remove(struct spi_device *spi)
{
	struct gxfp *g = spi_get_drvdata(spi);

	debugfs_remove_recursive(g->dbg);
	if (g->enable)
		gpiod_set_value_cansleep(g->enable, 0);
}

static const struct acpi_device_id gxfp_acpi_ids[] = {
	{ "GXFP51A0" }, { "GXFP51A7" }, { "GXFP5187" }, { }
};
MODULE_DEVICE_TABLE(acpi, gxfp_acpi_ids);

static const struct spi_device_id gxfp_spi_ids[] = {
	{ "gxfp51a0" }, { }
};
MODULE_DEVICE_TABLE(spi, gxfp_spi_ids);

static struct spi_driver gxfp_driver = {
	.driver = {
		.name = GXFP_NAME,
		.acpi_match_table = gxfp_acpi_ids,
	},
	.id_table = gxfp_spi_ids,
	.probe	= gxfp_probe,
	.remove	= gxfp_remove,
};
module_spi_driver(gxfp_driver);

MODULE_DESCRIPTION("Goodix GXFP51A0 SPI fingerprint sensor (bring-up)");
MODULE_LICENSE("GPL");
