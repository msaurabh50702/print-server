# Pi Print Server

Turn a Raspberry Pi into a Wi-Fi print server for a USB-only printer
(built for the **Canon i-SENSYS MF4820d**). Any phone, tablet or laptop on the
same network opens `http://<pi-name>.local` and prints. No apps or drivers
are needed on the phone or laptop.

- **Print photos**: pick how many photos go on one A4 page (1, 2, 4, 6, 8, 9, 12 or 16).
  Tap each box in the grid to add a photo, then crop, rotate, change or remove it.
  "Fill all" repeats one photo in every empty box, which is handy for passport-style sheets.
- **Free size**: add one or more photos, crop them, then place them anywhere on a blank A4 page.
  Drag to move, pull the corner (or pinch) to resize, or type an exact width/height in cm. The size
  shown is the printed size. It also has centre snapping, *Fit page*, *Duplicate* and a warning when
  something is outside the printable area.
- **Passport photos**: add one photo, crop it with a face-position oval guide, pick the size
  (35 × 45 mm for India/UK/EU, 2 × 2 in for the US, or 20 × 25 mm stamp size) and how many
  (4, 8, 12 or a full page). Photos are tiled at real size from the top-left corner with cutting guides.
- **ID card copy**: photograph or upload the front and back of an ID card (Aadhaar, PAN,
  driving licence, bank card and similar), crop each one to the card's edges, and print both on one
  A4 page at 125.6 × 94 mm each (set by `ID_CARD_MM` in `photos.py`), with an optional cutting guide.
- **Print documents**: select one or several PDF, Word, Excel, PowerPoint, text or image files
  at once (up to 20). Reorder or remove them, preview the pages, and optionally set a page range
  for each document. Then print them all with shared copies and one- or two-sided settings.
  Each document is sent as its own print job, in list order.
- **Several printers**: every print page has a printer picker (for example the Canon laser and an
  HP DeskJet inkjet). It remembers your choice per phone and only shows options the printer supports:
  **Colour / Black & white** for colour printers and **Two-sided** for printers with automatic duplex.
- **Paper and quality**: printers that offer them get *Paper* (e.g. Plain / Photo glossy) and
  *Quality* (Draft / Normal / Best, or *Toner save* on Canon lasers) menus, remembered per printer.
- **Ink, toner and paper warnings**: the status pill shows alerts like *Out of paper* or *Black ink low*,
  and the queue page shows ink/toner levels for printers that report them.
- **HEIC photos** (iPhone / Samsung) work everywhere; the Pi converts them when the phone's browser can't.
- **Documents: Fit to page or Actual size.** Use *Actual size* for forms that must print at their exact size.
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

### Setting up the Pi from a fresh SD card

These steps set up a Pi with no monitor, keyboard or mouse: everything is done over SSH from
another computer (the commands below are for a Mac; Linux is the same).

1. **Write the SD card.** In **Raspberry Pi Imager** choose *Device* → your Pi, *Operating System* →
   Raspberry Pi OS (other) → **Raspberry Pi OS Lite (64-bit)**, and your SD card. Under
   *Next → Edit settings*:
   - *General*: hostname `printer`, a username (e.g. `pi`) and password, your Wi-Fi name and
     password with the right Wi-Fi country, and your time zone. A Pi 3 only sees **2.4 GHz** Wi-Fi.
   - *Services*: **Enable SSH** with password authentication.

   Older 32-bit "Raspbian" installs (Buster and earlier) won't work: their package servers are gone
   and Canon's driver is 64-bit only. `install.sh` checks for this (and for a wrong clock) before
   installing anything.
2. **First start.** Put the card in the Pi (remove any USB drive it used to start from), power it on,
   and **wait about 5 minutes without unplugging it**: the first start resizes the card, applies your
   settings and restarts. Pulling the power during this can damage the card. Then connect:
   ```bash
   ssh-keygen -R printer.local      # forget the old fingerprint if this name was used before
   ssh pi@printer.local
   ```
   - *"Connection refused"*: the Pi is on the network but SSH isn't running yet. Wait a few more
     minutes. If it persists, put the card back in the computer, run `touch /Volumes/bootfs/ssh`,
     and start the Pi again.
   - *"cannot resolve printer.local"*: the Pi isn't on the network (usually a wrong Wi-Fi name or
     password). Connect it to the router with a network cable and try again. To find its address,
     run `arp -a | grep -i -E "b8:27:eb|printer"` on the Mac (`b8:27:eb` is used by every Pi 3) and
     `ssh pi@<that address>`.
3. **Fix Wi-Fi if needed** (only when you had to use a cable). On the Pi:
   ```bash
   sudo raspi-config nonint do_wifi_country IN     # your two-letter country code
   sudo rfkill unblock wifi
   nmcli dev wifi list                             # your network must be listed
   nmcli -t -f NAME,TYPE con show                  # delete old Wi-Fi entries with a wrong password:
   sudo nmcli con delete "<name>"
   sudo nmcli --ask dev wifi connect "<Wi-Fi name>"   # type the password when asked
   hostname -I                                     # shows one address per connection
   ```
   `--ask` avoids problems with characters like `!` or `$` in the password.
4. **Install the print server** (see [Install](#install)), **add the printers**
   (see [Add the printer](#add-the-printer)) and **turn on HTTPS**
   (see [Install it as an app](#install-it-as-an-app-https)).
5. **Unplug the network cable** (if you used one), run `sudo reboot`, and check that
   `http://printer.local` opens on your phone over Wi-Fi.
6. **On each phone** (again, if you are re-installing): a fresh install creates a **new** HTTPS
   certificate, so remove the old app and the old *Caddy Local Authority* certificate
   (Android: Settings → search "User credentials"), then install the new certificate from
   `http://printer.local/install` and install the app again from `https://printer.local`.

Shut the Pi down before unplugging it, so the SD card isn't damaged: use **Print queue → Shut down**
in the app (see [Restart or shut down the Pi](#restart-or-shut-down-the-pi)) or `sudo poweroff`.

## Install

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/msaurabh50702/print-server.git
cd print-server
./install.sh                 # --no-office skips LibreOffice (PDF/images still work)
```

`install.sh` installs CUPS, poppler-utils, LibreOffice (for Office files) and Avahi. It
then creates a Python virtualenv and starts the `print-server` systemd service on port 80.

### Add the printer

Copy Canon's ARM64 driver `.deb` to the Pi (from the computer you downloaded it to):

```bash
scp ~/Downloads/linux-UFRII-drv-*/ARM64/Debian/cnrdrvcups-ufr2-uk_*_arm64.deb pi@printer.local:~
```

Connect the printer by USB, switch it on, and run on the Pi:

```bash
./scripts/setup-printer.sh canon ~/cnrdrvcups-ufr2-uk_*_arm64.deb
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

**Share to Printer (Android):** once installed, *Printer* appears in the Share menu of other apps
(Gallery, WhatsApp, Files, Gmail…). Shared documents open in *Print documents*; shared photos offer
*Photo page*, *Passport photos*, *ID card copy* (first photo = front, second = back) or *Print as documents*.

### Works offline

The installed app (from `https://`) keeps itself on the phone, so it opens and works even when the
Pi can't be reached: weak Wi-Fi, the Pi switched off, or the phone away from home. Open the app once
on Wi-Fi and wait until the home page says **✓ Saved on this phone: works offline** (after an
update, again).

- A banner says the printer server isn't reachable, and the status shows how many jobs are saved.
- **Print** saves the job on the phone. It's sent automatically as soon as the Pi can be reached,
  and printed straight away (or when the printer is switched on).
- Photo pages (photos, free size, passport, ID card) work fully offline, including crop and rotate.
- Documents: PDFs and images are previewed on the phone. Word, Excel and PowerPoint files can be
  queued, but are previewed only once the Pi is reachable, because the Pi converts them.
- **Print queue → Saved on this phone** lists the saved jobs, with **Send now** and **Delete**.
- Every job carries a unique key, so a job is never printed twice, even if the Wi-Fi drops
  after the Pi received it and the phone sends it again.
- On mobile data or very weak Wi-Fi the phone first checks (for up to 4 seconds) that the Pi
  answers, so nothing hangs. The Print button never spins for more than 20 seconds; a slow upload
  finishes in the background. Deleting a saved job also stops a send that's in progress.

Offline, **PDF download**, HEIC photos, the queue and ink levels need the Pi. On Android, saved
jobs can also be sent after the app is closed; on iPhone they're sent while the app is open.
After an update (`git pull` and a restart), the app on the phone updates itself on its next visit.

### Restart or shut down the Pi

The Pi has no power button, so the app has one: open **Print queue** and scroll to **Print server**.

- **Restart**: useful if printing gets stuck. The app is back in about a minute.
- **Shut down**: do this before unplugging the Pi. Wait until the green light stops flashing
  (about 20 seconds), then unplug. To turn it on again, plug the power back in.

`install.sh` allows the app to run exactly these two commands (`/etc/sudoers.d/print-server`). On a Pi
set up before this was added, run `./install.sh` again.

**Optional physical button:** connect a push button between pins **5** and **6** of the GPIO header
(GPIO 3 and ground), add `dtoverlay=gpio-shutdown` to `/boot/firmware/config.txt` and reboot. Pressing
it then shuts the Pi down, and pressing it again while shut down turns it back on.

### Status LEDs and buttons (optional)

Four LEDs and two buttons on the Pi's GPIO header show what's going on and fix common problems
without a phone or SSH. Wire them, then run `./scripts/setup-panel.sh` (it needs `python3-gpiozero`,
which it installs if missing: that needs internet once).

| Part | BCM pin | Physical pin | Meaning |
|---|---|---|---|
| POWER LED | GPIO 5 | 29 | slow blink: the Pi is running |
| NETWORK LED | GPIO 6 | 31 | on: has an address · slow blink: cable plugged in or Wi-Fi joined, waiting for the router to give an address · off: no cable and no Wi-Fi |
| SERVER LED | GPIO 13 | 33 | on: the app answers · fast blink: starting or not answering |
| PRINTER LED | GPIO 19 | 35 | on: ready · slow blink: printing · flicker: ink/toner/paper low · fast blink: switched off or error · off: none set up |
| FIX button | GPIO 26 | 37 | press: restart the printing services (and reconnect the network if there's no address) · hold 5 s: restart the Pi |
| POWER button | GPIO 3 | 5 | hold 3 s: shut down · press while shut down: start the Pi |
| Ground | | 39 (and 6 for the POWER button) | |

Each LED goes from its pin through a 220–330 Ω resistor (long leg towards the pin) to ground; each
button goes between its pin and ground. When a button is accepted, all LEDs light up until the action
is done. Pins can be changed in `/etc/default/print-server` (`PANEL_LED_POWER=…`, `PANEL_BUTTON_FIX=…`),
then `sudo systemctl restart print-panel`. `./scripts/setup-panel.sh --disable` removes it. If you add a
DS3231 clock module, it shares GPIO 3 with the POWER button; that works, but you can move the button
with `PANEL_BUTTON_POWER=21` (it then can't start a shut-down Pi).

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
  `photos.py` places the front in the top half and the back in the bottom half at `ID_CARD_MM` size.
  Jobs are sent without fit-to-page scaling, so the copy prints at 100%.
- `compose_passport` tiles one photo at its exact size. Sizes are defined in `PASSPORT_SIZES`
  in `photos.py`, so it's easy to add another country's size.
- `printserver/documents.py` converts uploads to PDF (LibreOffice for Office files, Pillow for images)
  and renders page previews with `pdftoppm`.
- `printserver/printing.py` sends jobs to CUPS with `lp`
  (`-o media=A4`, copies, page ranges, `sides=two-sided-long-edge` for duplex).

## Updating

```bash
cd ~/print-server && git pull && sudo systemctl restart print-server
```

## Troubleshooting

- **The status pill says "No printer"**: run `scripts/setup-printer.sh`, or set `PRINTER_NAME`.
- **The Wi-Fi has no internet** (the Pi and phones only talk to each other): it works, with two things to know.
  - *Android* may leave or ignore a Wi-Fi without internet. When it asks, choose **Stay connected**, and turn off
    automatic switching to mobile data (Samsung: Settings → Connections → Wi-Fi → ⋮ → Intelligent Wi-Fi →
    Switch to mobile data; other phones: in the Wi-Fi network's settings). Or turn off mobile data while printing.
  - *The Pi's clock*: a Pi has no clock battery, so without internet time it's behind after every power cut.
    The app sets it from the phone's clock automatically (allowed by `scripts/setup-permissions.sh`, which
    `install.sh` runs), and `enable-https.sh` makes certificates last 6 days instead of 12 hours, so a clock
    that's behind doesn't break HTTPS. For a clock that's always right, add a DS3231 real-time clock module.
- **The status pill says "Offline · N saved"**: the phone can't reach the Pi. The saved jobs print automatically once it can; see [Works offline](#works-offline).
- **The status pill says "Switched off"**: the USB printer is turned off or unplugged. Jobs sent now wait in the queue and print once it is back on.
- **The status pill says "Offline"**: check the printer is on and the USB cable is connected, then run `cupsenable Canon_MF4820d`.
- **Word/Excel files fail**: install LibreOffice: `sudo apt install libreoffice-writer libreoffice-calc libreoffice-impress`.
- **`.local` address doesn't open** (some Android phones): use the Pi's IP address instead, and consider a DHCP reservation on your router.
- **Photos print in grey**: the MF4820d is a mono laser printer, so colour photos print in greyscale.
