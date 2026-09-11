// SPDX-License-Identifier: GPL-2.0
/*
 * spisnoop - watch the Intel LPSS SSP's chip-select register while somebody
 *            else drives the bus.
 *
 * The question: on this platform CS_CONTROL (BAR+0x224) reports SW_MODE=1,
 * meaning *software* drives chip select - pxa2xx-spi must write that register
 * to assert CS before a transfer and deassert it after. We have only ever read
 * it while idle, where it sits at 0xe003 (CS deasserted).
 *
 * If it never changes while transfers run, CS is never asserted, the sensor
 * never sees a framed transaction, and it would correctly ignore everything -
 * which looks exactly like our symptom: correct configuration, transfers
 * "completing", eternal silence.
 *
 * This module deliberately binds to NO device, so spidev can keep the sensor
 * and drive real traffic underneath us. We only ioremap the controller's
 * register window and sample.
 *
 * Usage:
 *   insmod spisnoop.ko            (optionally phys=0x... ms=...)
 *   # in another shell, start continuous SPI traffic, then:
 *   cat /sys/kernel/debug/spisnoop/snap
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/io.h>
#include <linux/debugfs.h>
#include <linux/seq_file.h>
#include <linux/ktime.h>
#include <linux/delay.h>
#include <linux/slab.h>

#define SSCR0		0x000
#define SSCR1		0x004
#define SSSR		0x008
#define LPSS_SSP_REG	0x220
#define LPSS_CS_CTRL	0x224

#define SSCR0_SSE	BIT(7)		/* synchronous serial port enable */
#define SSSR_BSY	BIT(4)		/* busy */
#define CS_SW_MODE	BIT(0)
#define CS_STATE	BIT(1)		/* 1 = CS high (deasserted) */

static unsigned long phys = 0x9b804000;
module_param(phys, ulong, 0444);
MODULE_PARM_DESC(phys, "physical address of the LPSS SSP BAR0");

static unsigned int ms = 300;
module_param(ms, uint, 0644);
MODULE_PARM_DESC(ms, "sampling window in milliseconds");

#define MAX_EVENTS 64

struct ev {
	u64 t_ns;
	u32 cs;
	u32 sscr0;
	u32 sssr;
};

static struct dentry *dbg;

static int snap_show(struct seq_file *sf, void *unused)
{
	void __iomem *base;
	struct ev *ev;
	u64 t0, now;
	u32 cs, last_cs, sscr0, sssr;
	unsigned long samples = 0, sse_on = 0, bsy_on = 0, cs_low = 0;
	unsigned int nev = 0, i;

	base = ioremap(phys, 0x1000);
	if (!base) {
		seq_puts(sf, "ioremap failed\n");
		return 0;
	}
	ev = kcalloc(MAX_EVENTS, sizeof(*ev), GFP_KERNEL);
	if (!ev) {
		iounmap(base);
		return -ENOMEM;
	}

	last_cs = readl(base + LPSS_CS_CTRL);
	t0 = ktime_get_ns();

	for (;;) {
		now = ktime_get_ns();
		if (now - t0 >= (u64)ms * 1000000ULL)
			break;

		cs    = readl(base + LPSS_CS_CTRL);
		sscr0 = readl(base + SSCR0);
		sssr  = readl(base + SSSR);
		samples++;

		if (sscr0 & SSCR0_SSE)
			sse_on++;
		if (sssr & SSSR_BSY)
			bsy_on++;
		if (!(cs & CS_STATE))
			cs_low++;

		if (cs != last_cs && nev < MAX_EVENTS) {
			ev[nev].t_ns  = now - t0;
			ev[nev].cs    = cs;
			ev[nev].sscr0 = sscr0;
			ev[nev].sssr  = sssr;
			nev++;
			last_cs = cs;
		}

		/* stay preemptible; we are sampling, not polling hard */
		if (!(samples & 0x3ff))
			cond_resched();
	}

	seq_printf(sf, "window        %u ms\n", ms);
	seq_printf(sf, "samples       %lu  (%lu/ms)\n", samples,
		   ms ? samples / ms : 0);
	seq_puts(sf, "\n");
	seq_printf(sf, "SSCR0.SSE set (controller enabled)  : %lu  (%lu%%)\n",
		   sse_on, samples ? sse_on * 100 / samples : 0);
	seq_printf(sf, "SSSR.BSY set  (transfer in flight)  : %lu  (%lu%%)\n",
		   bsy_on, samples ? bsy_on * 100 / samples : 0);
	seq_printf(sf, "CS asserted   (CS_CONTROL bit1 = 0) : %lu  (%lu%%)\n",
		   cs_low, samples ? cs_low * 100 / samples : 0);
	seq_puts(sf, "\n");
	seq_printf(sf, "CS_CONTROL transitions observed: %u\n", nev);
	for (i = 0; i < nev; i++)
		seq_printf(sf, "  t=%9llu ns  CS_CONTROL=0x%08x  SSCR0=0x%08x  SSSR=0x%08x\n",
			   ev[i].t_ns, ev[i].cs, ev[i].sscr0, ev[i].sssr);

	seq_puts(sf, "\nverdict: ");
	if (!sse_on && !bsy_on)
		seq_puts(sf, "no traffic seen at all - was anything driving the bus?\n");
	else if (nev == 0 && !cs_low)
		seq_puts(sf, "*** traffic ran but CS NEVER ASSERTED - chip select is not being driven ***\n");
	else if (nev == 0 && cs_low == samples)
		seq_puts(sf, "*** CS held asserted the whole time - never deasserted ***\n");
	else
		seq_puts(sf, "CS is toggling normally around transfers.\n");

	kfree(ev);
	iounmap(base);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(snap);

static int __init spisnoop_init(void)
{
	dbg = debugfs_create_dir("spisnoop", NULL);
	debugfs_create_file("snap", 0400, dbg, NULL, &snap_fops);
	pr_info("spisnoop: watching LPSS SSP at 0x%lx, window %u ms\n", phys, ms);
	return 0;
}

static void __exit spisnoop_exit(void)
{
	debugfs_remove_recursive(dbg);
}

module_init(spisnoop_init);
module_exit(spisnoop_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Sample Intel LPSS SSP chip-select while another driver owns the bus");
