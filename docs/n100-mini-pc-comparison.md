# N100 mini PC comparison for a DNS server

Research checked 2026-10-27. This is a hardware comparison, not a deployment decision.

**Shortlist the ASUS PN42 for a fanless appliance, the Minisforum UN100L for low measured idle power, and the GMKtec G3 for replaceable RAM at a low purchase price.** The MINIX Z100-0dB is another fanless option, but its current direct-store price and availability weaken its value. These are judgments based on the evidence below.

## Hardware and measured power

**Compare complete systems and exact processor variants.** The measurements come from different operating systems and test configurations. They are observations of review samples, not guaranteed DNS-server consumption.

| Model | Memory and storage | Cooling and network | Idle evidence | Assessment |
|---|---|---|---|---|
| ASUS ExpertCenter PN42 | N100/N200 series has one replaceable DDR4 SO-DIMM, officially up to 16GB; M.2 2280 NVMe | Fanless; 2.5GbE, optional second port | ServeTheHome measured **5–6W on its N200 sample**, not the N100 | Good appliance candidate if complete price is reasonable. Check the SKU carefully. |
| Minisforum UN100L | Reviewed 8GB LPDDR5 soldered; 16GB configuration offered; replaceable SSD | Fan cooled; reviewed sample has Gigabit Ethernet | Notebookcheck measured **3.8/4.8/9.8W minimum/average/maximum idle** on an 8GB Windows sample | Strong low-idle candidate. Buy 16GB initially because RAM cannot be upgraded. |
| MINIX Z100-0dB | One DDR4 SO-DIMM, vendor supports up to 32GB; replaceable NVMe | Fanless; one 2.5GbE port | CNX measured **7.5–7.6W at the wall under Ubuntu 22.04** | Useful combination of silence and expandable memory, but expensive direct. |
| GMKtec NucBox G3, N100 | One DDR4 SO-DIMM, vendor supports up to 32GB; NVMe 2280 plus SATA 2242 expansion | One 2.5GbE port; fan cooled | **Unproven in this research:** no sufficiently documented original measurement selected | Value candidate only after checking the full configuration price. Do not substitute CPU TDP for idle draw. |

Sources for ASUS: [manufacturer datasheet](https://www.asus.com/Websites/us/products/e0zqzw9nhws2tdfp/pdf/o8rvvujo6m7jqhjy.pdf), [original ServeTheHome power measurements](https://www.servethehome.com/asus-expertcenter-pn42-review-a-different-type-of-fanless-mini-pc/4/). The currently visible [UK specification page](https://www.asus.com/uk/displays-desktops/mini-pcs/pn-series/asus-expertcenter-pn42/techspec/) describes an N200 barebone, illustrating why the model name alone is insufficient.

Sources for UN100L: [Notebookcheck's original review and teardown](https://www.notebookcheck.net/Minisforum-UN100L-review-The-mini-PC-for-office-tasks-with-an-Intel-N100-and-DDR5-RAM-costs-less-than-US-200.797190.0.html). Its historical launch price is not a current offer. The reported idle values are AC-adapter system measurements, not CPU package power.

Sources for MINIX: [manufacturer specifications](https://www.minix.us/z100-0db), [original Ubuntu test](https://www.cnx-software.com/2024/03/09/minix-z100-0db-review-fanless-intel-n100-mini-pc-ubuntu-22-04/).

Sources for G3: [manufacturer EU specifications](https://de.gmktec.com/en/products/gmktec-nucbox-g3-intel%C2%AE-alder-lake-n100), [manufacturer US listing](https://www.gmktec.com/products/nucbox-g3-most-cost-effective-mini-pc-with-intel-n100-processor). Confirm the original N100 G3: similarly named G3 Plus, G3S, G3 Pro and G3 Ultra use different processors.

## Price and availability snapshot

**No verified in-stock US 16GB purchase offer was established for these four models.** Direct pages still exist, but a visible price does not establish availability for every selectable variant.

- GMKtec US showed **$169.99 for the selected barebone**, without RAM, SSD or OS, and marked it sold out. A 16GB/512GB selector was present, but its price and stock were not verified. [Official listing](https://www.gmktec.com/products/nucbox-g3-most-cost-effective-mini-pc-with-intel-n100-processor)
- MINIX's official store showed **$345 for the selected 8GB/256GB US variant**, marked sold out. A 16GB/512GB option existed, but its live price and availability were not verified. [Official listing](https://www.minix.com.hk/products/minix-z100-0db-fanless-mini-pc)
- ASUS UK showed **£299.99 for an N200 barebone** with a notification option. This is not a US N100 quote and excludes memory and storage. [Official listing](https://www.asus.com/uk/displays-desktops/mini-pcs/pn-series/asus-expertcenter-pn42/techspec/)
- Minisforum's [official collection listing](https://www.minisforum.com/collections/elitemini-series) showed UN100L at **$199, sold out**. That collection price does not establish a particular RAM/SSD configuration. The review's sub-$200 price is from January 2024.

## Alternative considered

**The Beelink EQ12 is less attractive when minimizing DNS-server idle power is the priority.** CNX measured Ubuntu idle at 13.9W with one Ethernet connection and 14.3W with two. Its dual 2.5GbE ports may justify considering it for other roles. These figures do not establish how an updated headless Linux installation will behave. [Original EQ12 review](https://www.cnx-software.com/2023/05/16/beelink-eq12-review-intel-processor-n100-mini-pc-windows-11-truenas-pfsense-ubuntu/?amp=1)

## How to choose

**16GB provides generous room for a dedicated DNS service; reserve 32GB for additional workloads.** This is a sizing recommendation, not an official Technitium requirement. For the existing application's measured memory evidence, see the owning [Technitium findings](../stacks/technitium/README.md#findings-2026-10-06-1560-laptop-instance).

At $0.15/kWh, each continuous watt costs $1.314/year. A 3W saving is about $3.94/year, so a large purchase premium is difficult to recover through electricity savings alone. Calculation: watts × 8,760 / 1,000 × electricity rate.

Before ordering, verify the exact CPU, included RAM and SSD, return policy, and automatic restart after power loss. For a dedicated DNS role, one Ethernet port is sufficient. Measure the finished headless Linux installation at the wall before the return period expires; its consumption remains unproven until then.
