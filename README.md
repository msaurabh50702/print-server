# Pi Print Server

Turn a Raspberry Pi into a Wi-Fi print server for a USB-only printer
(built for the **Canon i-SENSYS MF4820d**). Any phone, tablet or laptop on the
same network opens `http://<pi-name>.local` and prints. No apps or drivers
are needed on the phone or laptop.

- **Print photos**: pick how many photos go on one A4 page (1, 2, 4, 6, 8, 9, 12 or 16).
  Tap each box in the grid to add a photo, then crop, rotate, change or remove it.
  "Fill all" repeats one photo in every empty box, which is handy for passport-style sheets.
- **Passport photos**: add one photo, crop it with a face-position oval guide, pick the size
  (35 × 45 mm for India/UK/EU, 2 × 2 in for the US, or 20 × 25 mm stamp size) and how many
  (4, 8, 12 or a full page). Photos are tiled at real size from the top-left corner with cutting guides.
- **ID card copy**: photograph or upload the front and back of an ID card (Aadhaar, PAN,
  driving licence, bank card and similar), crop each one to the card's edges, and print both on one
  A4 page at real card size (85.6 × 54 mm), with an optional cutting guide.
- **Print documents**: select one or several PDF, Word, Excel, PowerPoint, text or image files
  at once (up to 20). Reorder or remove them, preview the pages, and optionally set a page range
  for each document. Then print them all with shared copies and one- or two-sided settings.
  Each document is sent as its own print job, in list order.
- **Several printers**: every print page has a printer picker (for example the Canon laser and an
  HP DeskJet inkjet). It remembers your choice per phone and only shows options the printer supports:
  **Colour / Black & white** for colour printers and **Two-sided** for printers with automatic duplex.
- **Print queue**: tap the status pill to see what's printing or waiting on each printer, cancel jobs,
  and see recently sent jobs.
- Mobile-first web UI with a sticky bottom action bar, a touch crop editor,
  dark mode and "add to home screen" support.
- The Pi also shares the printer over CUPS/IPP, so computers can add it as a normal
  network printer.

```
 phone / laptop ──Wi-Fi──▶ Raspberry Pi (Flask + CUPS) ──USB──▶ Canon MF4820d
```

## Requirements

- Raspberry Pi 3/4/5 running **64-bit** Raspberry Pi OS (Bookworm or newer).
  Canon's Linux driver ships ARM64 packages. Check with `dpkg --print-architecture`, which should print `arm64`.
- The printer connected to the Pi by USB.
- Canon **UFR II / UFRII LT Printer Driver for Linux** (V5.x or newer), downloaded from
  Canon's support site (search "MF4820d" → Drivers & Downloads → Linux).

### Preparing the SD card

Use **Raspberry Pi Imager** and pick **Raspberry Pi OS Lite (64-bit)**. Under *Edit settings*, set the
hostname (e.g. `printer`), your user, Wi-Fi, time zone, and enable SSH. Older 32-bit "Raspbian"
installs (Buster and earlier) won't work: their package servers are gone and Canon's driver is 64-bit only.
`install.sh` checks for this (and for a wrong clock) before installing anything.

## Install

```bash
git clone https://github.com/msaurabh50702/print-server.git
cd print-server
./install.sh                 # --no-office skips LibreOffice (PDF/images still work)
```

`install.sh` installs CUPS, poppler-utils, LibreOffice (for Office files) and Avahi. It
then creates a Python virtualenv and starts the `print-server` systemd service on port 80.

### Add the printer

Copy Canon's ARM64 driver `.deb` to the Pi, then run:

```bash
./scripts/setup-printer.sh ~/cnrdrvcups-ufr2-uk_*_arm64.deb
```

The script installs the driver, finds the printer on USB, creates a CUPS queue called
`Canon_MF4820d` (A4, set as the default) and turns on printer sharing. Print a test page with:

```bash
lp /usr/share/cups/data/testprint
```

If the model isn't detected automatically, list the drivers with `lpinfo -m | grep -i canon`
and run the script again with `PPD=<name> ./scripts/setup-printer.sh canon`.

### Add more printers (e.g. HP DeskJet 3835)

Connect the HP by USB, or put it on the same Wi-Fi as the Pi, and run:

```bash
./scripts/setup-printer.sh hp
```

This installs HP's open-source driver (HPLIP) and adds a queue called `HP_DeskJet_3835`. It prefers
USB when both USB and Wi-Fi are available, and keeps the Canon as the default printer. For any other
printer, pass your own names and patterns:

```bash
QUEUE_NAME=Brother_HL URI_MATCH=Brother MODEL_MATCH='HL-L2350' ./scripts/setup-printer.sh custom
```

Every printer set up in CUPS appears in the web app's printer list. Nothing else needs configuring.

## Use it

On any device on the same Wi-Fi, open **http://raspberrypi.local**
(replace `raspberrypi` with your Pi's hostname), or use the IP address printed by `install.sh`.
To get a friendlier address, rename the Pi with
`sudo raspi-config` → System → Hostname, for example to `printer`, which gives `http://printer.local`.

### Install it as an app (HTTPS)

Chrome only offers **Install app** for secure (`https`) sites; over `http://printer.local` it can
only add a shortcut. To make it installable, run once on the Pi:

```bash
./scripts/enable-https.sh
```

This installs [Caddy](https://caddyserver.com), which creates the Pi's own certificate authority and
serves `https://<hostname>.local` (and the Pi's IP address) in front of the app. Plain `http://` keeps
working. Then on each phone open `http://<hostname>.local/install`: it walks you through downloading
and installing the certificate once, opening the `https` address and tapping **Install**.

- Run the script again if the Pi's IP address changes (or reserve the IP in your router).
- `./scripts/enable-https.sh --disable` goes back to plain http on port 80.
- iPhone: Safari → Share → **Add to Home Screen** already opens full screen without the certificate.

## Configuration

Edit `/etc/default/print-server`, then run `sudo systemctl restart print-server`.

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `80` | HTTP port |
| `PRINTER_NAME` | *(CUPS default)* | CUPS queue to print to |
| `DRY_RUN` | `0` | `1` saves PDFs to `DATA_DIR/dry-run` instead of printing |
| `DATA_DIR` | `/var/tmp/print-server` | Temporary uploads (deleted after 1 hour) |
| `CA_CERT_PATH` | `/etc/print-server/ca.crt` | Certificate offered at `/ca.crt` (set by `enable-https.sh`) |
| `MAX_UPLOAD_MB` | `100` | Upload size limit |
| `PAGE_MARGIN_MM` / `CELL_GAP_MM` | `5` / `3` | Photo sheet margin and gap between photos |

Logs: `journalctl -u print-server -f`. CUPS web admin: `http://<pi>:631`.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
DRY_RUN=1 PORT=8080 python run.py     # http://localhost:8080, nothing is really printed
pytest
```

### How it works

- `printserver/photos.py` builds the A4 sheet at 300 DPI with Pillow. Crop and rotate happen
  in the browser (`static/js/editor.js`), and the server receives one cropped JPEG per box.
- The ID card page reuses the same editor, locked to the ID-1 card shape. `compose_id_card` in
  `photos.py` places the front in the top half and the back in the bottom half at 85.6 × 54 mm.
  Jobs are sent without fit-to-page scaling, so the copy prints at 100%.
- `compose_passport` tiles one photo at its exact size. Sizes are defined in `PASSPORT_SIZES`
  in `photos.py`, so it's easy to add another country's size.
- `printserver/documents.py` converts uploads to PDF (LibreOffice for Office files, Pillow for images)
  and renders page previews with `pdftoppm`.
- `printserver/printing.py` sends jobs to CUPS with `lp`
  (`-o media=A4`, copies, page ranges, `sides=two-sided-long-edge` for duplex).

## Troubleshooting

- **The status pill says "No printer"**: run `scripts/setup-printer.sh`, or set `PRINTER_NAME`.
- **The status pill says "Offline"**: check the printer is on and the USB cable is connected, then run `cupsenable Canon_MF4820d`.
- **Word/Excel files fail**: install LibreOffice: `sudo apt install libreoffice-writer libreoffice-calc libreoffice-impress`.
- **`.local` address doesn't open** (some Android phones): use the Pi's IP address instead, and consider a DHCP reservation on your router.
- **Photos print in grey**: the MF4820d is a mono laser printer, so colour photos print in greyscale.
