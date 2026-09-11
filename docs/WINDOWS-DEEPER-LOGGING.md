# Logging deeper than the Goodix driver, on Windows

The Goodix driver's own log (see `docs/WINDOWS-LOGGING.md`) starts at
`DriverEntry` and shows the *protocol*. Everything **below** it — the Windows
SPB framework and Intel's SPI controller driver, which set the controller up
before Goodix ever speaks — has never been observed.

There is a way in, and it ships disabled on every machine.

## Intel's controller driver has its own debug channel

`ialpss2_spi_cnl.inf` registers one, and ships it switched off:

```
[iaLPSS2_SPI_ETW.AddReg]
HKLM,"...\WINEVT\Channels\Intel-iaLPSS2-SPI/Debug","OwningPublisher",0x0,"{6E112845-A8C4-4143-A631-256E8A3E7691}"
HKLM,"...\WINEVT\Channels\Intel-iaLPSS2-SPI/Debug","Enabled",0x00010001,0
```

- **Channel**: `Intel-iaLPSS2-SPI/Debug`
- **Provider GUID**: `{6E112845-A8C4-4143-A631-256E8A3E7691}`
- **Enabled**: `0` by default

This is the layer that programs the SSP, drives chip select, and moves the FIFOs
— the layer Linux implements with `pxa2xx-spi`. It is exactly the comparison we
cannot make from Linux.

## Turning it on

Elevated PowerShell or cmd:

```
wevtutil sl "Intel-iaLPSS2-SPI/Debug" /e:true
```

Then reboot (the controller is only configured once, at device start, so the
interesting events happen during boot).

Read it back with:

```
wevtutil qe "Intel-iaLPSS2-SPI/Debug" /f:text /c:2000 > C:\ialpss-debug.txt
```

To turn it off again:

```
wevtutil sl "Intel-iaLPSS2-SPI/Debug" /e:false
```

## Capturing it as a live trace instead

If the channel yields little, capture the provider directly — this catches
events the channel may not persist:

```
logman create trace ialpss -p "{6E112845-A8C4-4143-A631-256E8A3E7691}" 0xffffffff 5 -o C:\ialpss.etl -ets
:: reproduce a fingerprint touch here
logman stop ialpss -ets
```

`C:\ialpss.etl` can then be decoded with `tracerpt C:\ialpss.etl -o C:\ialpss.xml`
or opened in Windows Performance Analyzer.

## What would actually be worth finding

The Linux side is fully accounted for: every readable register is correct,
chip select frames each transfer properly, the receive path is proven by
internal loopback, and both stacks place the LPSS private block at the same
offset. So this is not a fishing trip — there are specific questions:

1. **Does Intel's driver write any register Linux never touches?** The private
   block at base+0x200 is mostly zeros on Linux. If Windows writes something
   there that Linux leaves alone, that is the difference.
2. **Does it use DMA where Linux uses PIO?** `iaLPSS2_SPI.sys` contains
   `DMA_ENABLER_CONTEXT` and `DMA_TRANSACTION_CONTEXT`; Linux uses programmed
   I/O for small transfers.
3. **What does it do with `base+0x800`?** Windows keeps a pointer to that region
   (context offset `0xc8`). On Linux it reads as entirely zero and `pxa2xx-spi`
   does not use it.
4. **Is there any step between controller setup and the first Goodix command**
   that we have never seen?

## Honest expectation

Low. Every comparison between the two stacks so far has produced *agreement*,
not difference — pad configuration, controller registers, chip-select framing,
private-block offset, protocol bytes, framing style. This is the last layer
where a difference could still hide, which is the only reason it is worth
looking.

It is also the only remaining investigation that does not require a logic
analyser.
