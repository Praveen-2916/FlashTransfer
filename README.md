# FlashTransfer

FlashTransfer is a python based windows application that I've built to transfer files from Android phones to Windows PC through ADB (Android debug bridge) over USB.

Windows file explorer using MTP (Media transfer protocol) for transferring files which takes more time. Using ADB is a simpler alternative that could provide better transfer speeds.

To use FlashTransfer, it is necessary that Android SDK Platform-Tools (ADB) is installed in the PC and USB debugging is enabled from the developer options in the android phone.

## Features

- Browse files and folders on your Android phone.
- View file sizes before transferring.
- Choose a destination folder on your PC.
- Monitor transfer percentage and speed in real time.
- Cancel transfers when needed.
- Background transfers so the interface remains responsive.
- Basic validation to check the transferred file's size.

## Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/Praveen-2916/FlashTransfer.git
cd FlashTransfer
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set up ADB

Download Android SDK Platform-Tools from the official Android developer website:

https://developer.android.com/tools/releases/platform-tools

### 4. Connect your phone

- Enable Developer options and USB debugging on your phone.
- Connect it to your PC using a USB cable.
- Accept the USB debugging authorization prompt.

### 5. Run FlashTransfer

Using the Windows launcher:

```bat
run.bat
```

Or run it directly:
```bash
python -m flashtransfer
```

## How to use

1. Connect your phone and launch FlashTransfer.
2. Browse your phone's storage and select a file.
3. Choose the destination folder on your PC.
4. Click Start Transfer.
5. Monitor the progress percentage and transfer speed.

## Performance

I tested FlashTransfer against Windows file explorer's MTP transfer using my android Redmi 12 phone and Windows 11 laptop

For a file size of 2.34 GB, Windows MTP took 91 seconds while FlashTransfer took 65 seconds.

For a file size of 8.24 GB, Windows MTP took 316 seconds while FlashTransfer took just 230 seconds

Across my tests, **FlashTransfer reduced file transfer time by approximately 27% compared to Windows File Explorer's MTP transfer.** Actual performance may vary depending on the device, USB connection, and destination drive.

## Project structure

```text
FlashTransfer/
├── flashtransfer/
│   ├── __main__.py
│   ├── app.py
│   ├── adb_client.py
│   └── transfer.py
├── .gitignore
├── README.md
├── requirements.txt
└── run.bat
```

- `app.py` handles the graphical interface.
- `adb_client.py` handles ADB commands and browsing phone storage.
- `transfer.py` manages transfers, progress updates, and cancellation.
