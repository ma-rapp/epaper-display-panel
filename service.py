import io
import logging
import pathlib
import signal
import sys
import threading
import time

import gpiozero
import requests
import yaml
from PIL import Image, ImageChops

from waveshare_epd import epd7in5_V2, epdconfig

HERE = pathlib.Path(__file__).parent


class GracefulKiller:
    kill_now = False

    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, signum, frame):
        logging.debug("received exit signal")
        self.kill_now = True


class ButtonMonitorThread(threading.Thread):
    def __init__(self, killer, display_thread):
        super().__init__()
        self.killer = killer
        self.display_thread = display_thread
        self.button_left = gpiozero.Button(20, bounce_time=0.1)
        self.button_right = gpiozero.Button(16, bounce_time=0.1)

    def button_left_pressed(self):
        logging.debug("left button pressed")
        self.display_thread.switch_to_next_app()

    def button_right_pressed(self):
        logging.debug("right button pressed")
        self.display_thread.switch_to_next_screen()

    def run(self):
        try:
            self.button_left.when_pressed = self.button_left_pressed
            self.button_right.when_pressed = self.button_right_pressed

            logging.info("button monitor registered events")
            while not self.killer.kill_now:
                time.sleep(1)
            logging.info("button monitor exits")
        except Exception:
            logging.exception("button monitor thread crashed")
            raise


class DisplayThread(threading.Thread):
    def __init__(self, server_url, killer):
        super().__init__()
        self.server_url = server_url
        self.killer = killer
        self.current_app = 0
        self.current_screen_per_app = {}

        self.metainfo = None
        self.last_online_check = None
        self.last_image = None
        self.force_update = False
        self.last_app_switch = time.time()

    def download_recent_image(self, app, screen):
        url = f"{self.server_url}/app/{app}/{screen}.png"
        r = requests.get(url, stream=True)
        if r.status_code == 200:
            return Image.open(io.BytesIO(r.content))
        else:
            logging.error(f"could not download recent image, got {r.status_code}")
            return None

    def images_differ(self, img1, img2):
        if img1 is None or img2 is None:
            return True
        if img1.size != img2.size:
            return True

        diff = ImageChops.difference(img1, img2)

        return bool(diff.getbbox())

    def display(self, epd, image):
        try:
            epd.init()

            screen = Image.new(
                "1", (epd.width, epd.height), 255
            )  # 255: clear the frame
            screen.paste(
                image,
                (
                    epd.width // 2 - image.width // 2,
                    epd.height // 2 - image.height // 2,
                ),
            )
            epd.display(epd.getbuffer(screen))
        finally:
            epd.sleep()

    def get_metainfo(self):
        if self.metainfo is None:
            url = f"{self.server_url}/info.json"
            r = requests.get(url)
            if r.status_code == 200:
                logging.info(f"get metainfo: {r.json()}")
                return r.json()
            else:
                logging.error(
                    f"could not read info.json from server, got {r.status_code}"
                )
                raise Exception()

    def get_nb_apps(self):
        metainfo = self.get_metainfo()
        return len(metainfo["apps"])

    def get_nb_screens(self, current_app):
        metainfo = self.get_metainfo()
        return metainfo["apps"][current_app]["nb_screens"]

    def switch_to_next_app(self):
        logging.info("switch to next app")
        self.last_app_switch = time.time()
        self.current_app = (self.current_app + 1) % self.get_nb_apps()
        self.current_screen_per_app[self.current_app] = self.current_screen_per_app.get(
            self.current_app, 0
        ) % self.get_nb_screens(self.current_app)
        self.force_update = True

    def switch_to_next_screen(self):
        logging.info("switch to next app")
        self.current_screen_per_app[self.current_app] = (
            self.current_screen_per_app.get(self.current_app, 0) + 1
        ) % self.get_nb_screens(self.current_app)
        self.force_update = True

    def should_update(self):
        should_update = False
        if self.force_update:
            self.force_update = False
            should_update = True
            logging.info("should update because of forced update")
        elif (
            self.last_online_check is None or time.time() - self.last_online_check > 60
        ):
            should_update = True
            logging.info("should update because last update was long time ago")
        return should_update

    def update(self, epd):
        image = self.download_recent_image(
            self.current_app, self.current_screen_per_app.get(self.current_app, 0)
        )
        self.last_online_check = time.time()

        if self.images_differ(self.last_image, image):
            logging.info("image changed")
            self.display(epd, image)
            self.last_image = image
        else:
            logging.info("image did not change")

    def should_switch_to_next_app_automatically(self):
        return time.time() > self.last_app_switch + 60 * 60  # switch every 60 minutes

    def run(self):
        try:
            rpi = epdconfig.RaspberryPi()
            epd = epd7in5_V2.EPD(rpi)
            while not self.killer.kill_now:
                if self.should_update():
                    self.update(epd)
                if self.should_switch_to_next_app_automatically():
                    logging.info("trigger switch to next app after timeout")
                    self.switch_to_next_app()
                time.sleep(1)
            logging.info("display thread exits")
        except Exception:
            logging.exception("display thread crashed")
            raise
        finally:
            rpi.module_exit(cleanup=True)


def load_config():
    with open(HERE / "config.yaml") as f:
        return yaml.safe_load(f)


def main():
    success = True
    killer = GracefulKiller()
    try:
        logging.basicConfig(
            filename=pathlib.Path(HERE / "logs/service.log"),
            format="%(asctime)s %(levelname)-8s %(name)-8s %(message)s",
            level=logging.DEBUG,
        )

        logging.info("starting")
        config = load_config()
        display_thread = DisplayThread(config["server_url"], killer)
        display_thread.start()
        button_monitor_thread = ButtonMonitorThread(killer, display_thread)
        button_monitor_thread.start()
        all_threads = [display_thread, button_monitor_thread]

        try:
            while not killer.kill_now:
                time.sleep(1)
                for thread in all_threads:
                    if not thread.is_alive():
                        logging.warning("one thread died -> exit")
                        killer.kill_now = True
                        success = False
        except KeyboardInterrupt:
            killer.kill_now = True

        logging.info("exiting")
        for thread in all_threads:
            thread.join()
        logging.info("exit")
    except Exception:
        logging.exception("service crashed")
        success = False

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
