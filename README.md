# Pi Print Server

Turn a Raspberry Pi into a Wi-Fi print server for a USB-only printer
(built for the **Canon i-SENSYS MF4820d**). Any phone, tablet or laptop on the
same network opens `http://<pi-name>.local` and prints. No apps or drivers
are needed on the phone or laptop.

- **Print photos**: pick how many photos go on one A4 page (1, 2, 4, 6, 8, 9, 12 or 16).
  Tap each box in the grid to add a photo, then crop, rotate, change or remove it.
  "Fill all" repeats one photo in every empty box, which is handy for passport-style sheets.
- **Print document**: upload a PDF, Word, Excel, PowerPoint, text or image file.
  Check the page previews, then print with copies, a page range and one- or two-sided options.
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

## Install

```bash
git clone https://github.com/msaurabh50702/print-server.git
cd print-server
./install.sh                 # add --no-office to skip LibreOffice (PDF/images still work)
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
and run the script again with `PPD=<name> ./scripts/setup-printer.sh`.

## Use it

On any device on the same Wi-Fi, open **http://raspberrypi.local**
(replace `raspberrypi` with your Pi's hostname), or use the IP address printed by `install.sh`.
To get a friendlier address, rename the Pi with
`sudo raspi-config` → System → Hostname, for example to `printer`, which gives `http://printer.local`.

## Configuration

Edit `/etc/default/print-server`, then run `sudo systemctl restart print-server`.

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `80` | HTTP port |
| `PRINTER_NAME` | *(CUPS default)* | CUPS queue to print to |
| `DRY_RUN` | `0` | `1` saves PDFs to `DATA_DIR/dry-run` instead of printing |
| `DATA_DIR` | `/var/tmp/print-server` | Temporary uploads (deleted after 1 hour) |
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
