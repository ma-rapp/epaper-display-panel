# ePaper Display Panel

This package contains the code to control an ePaper panel.
The display is organized in separate apps.
Each app can display several screens.

The screen images are rendered on a server and simply downloaded for display.
That allows to keep this module as lean as possible, as this is running on a Raspberry Pi Zero 2W.
For the server, see [epaper-display-server](https://github.com/ma-rapp/epaper-display-server).

The panel supports two buttons, where the left button switches between apps and the right button switches screen within the current app.

## Installation

Tested on Raspberry Pi OS Lite.

To set up:
1. Configure server url in `config.yaml`
2. Configure paths in `panel.service`
3. Install required packages
    ```bash
    sudo apt install libgpiod-dev libsystemd-dev swig
    ```
4. Install uv
5. Setup virtual environment
    ```bash
    uv sync --no-dev
    ```
6. Enable service
    ```bash
    loginctl enable-linger pi

    systemctl --user link /home/pi/projects/epaper-display-panel/panel.service
    systemctl --user enable panel
    systemctl --user start panel
    ```
7. Setup periodic tasks
    ```bash
    crontab -e
    ```
    Add following entry to:
    - check for updates to the environment every day
    ```
    1 1 * * * (date && cd ~/projects/epaper-display-panel && /home/pi/.local/bin/uv lock --upgrade) >> ~/projects/epaper-display-panel/logs/uv-update.log 2>&1
    ```

To disable the service:
1. Run
    ```
    systemctl --user stop panel
    systemctl --user disable panel
    ```

## Development

```
uv sync --dev
uv run pre-commit install
```
