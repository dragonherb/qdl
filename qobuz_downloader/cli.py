import configparser
import hashlib
import logging
import glob
import os
import sys

from qobuz_downloader.bundle import Bundle
from qobuz_downloader.color import GREEN, RED, YELLOW
from qobuz_downloader.commands import qdl_args
from qobuz_downloader.core import QobuzDL
from qobuz_downloader.downloader import DEFAULT_FOLDER, DEFAULT_TRACK

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)

if os.name == "nt":
    OS_CONFIG = os.environ.get("APPDATA")
else:
    OS_CONFIG = os.path.join(os.environ["HOME"], ".config")

CONFIG_PATH = os.path.join(OS_CONFIG, "QDL")
CONFIG_FILE = os.path.join(CONFIG_PATH, "config.ini")
QOBUZ_DB = os.path.join(CONFIG_PATH, "QDL.db")


def _reset_config(config_file):
    logging.info(f"{YELLOW}Creating config file: {config_file}")
    config = configparser.ConfigParser()
    config["DEFAULT"]["email"] = input("Enter your email:\n- ")
    password = input("Enter your password\n- ")
    config["DEFAULT"]["password"] = hashlib.md5(password.encode("utf-8")).hexdigest()
    config["DEFAULT"]["default_folder"] = (
        input("Folder for downloads (leave empty for default 'QDL Downloads')\n- ")
        or "QDL Downloads"
    )
    config["DEFAULT"]["default_quality"] = (
        input(
            "Download quality (5, 6, 7, 27) "
            "[320, LOSSLESS, 24B <96KHZ, 24B >96KHZ]"
            "\n(leave empty for default '6')\n- "
        )
        or "6"
    )
    config["DEFAULT"]["default_limit"] = "20"
    config["DEFAULT"]["no_m3u"] = "false"
    config["DEFAULT"]["albums_only"] = "false"
    config["DEFAULT"]["no_fallback"] = "false"
    config["DEFAULT"]["og_cover"] = "false"
    config["DEFAULT"]["embed_art"] = "false"
    config["DEFAULT"]["no_cover"] = "false"
    config["DEFAULT"]["no_database"] = "false"
    logging.info(f"{YELLOW}Getting tokens. Please wait...")
    bundle = Bundle()
    config["DEFAULT"]["app_id"] = str(bundle.get_app_id())
    config["DEFAULT"]["secrets"] = ",".join(bundle.get_secrets().values())
    config["DEFAULT"]["folder_format"] = DEFAULT_FOLDER
    config["DEFAULT"]["track_format"] = DEFAULT_TRACK
    config["DEFAULT"]["smart_discography"] = "false"
    # default_start_mode: Set to 'fun', 'dl', or 'lucky' to automatically start in that mode
    # when running qdl without arguments. Set to 'none' to always show help when no arguments.
    config["DEFAULT"]["default_start_mode"] = "none"
    with open(config_file, "w") as configfile:
        config.write(configfile)
    logging.info(
        f"{GREEN}Config file updated. Edit more options in {config_file}"
        "\nso you don't have to call custom flags every time you run "
        "a qdl command."
    )


def _remove_leftovers(directory):
    directory = os.path.join(directory, "**", ".*.tmp")
    for i in glob.glob(directory, recursive=True):
        try:
            os.remove(i)
        except:  # noqa
            pass


def _handle_commands(qobuz, arguments):
    try:
        if arguments.command == "dl":
            qobuz.download_list_of_urls(arguments.SOURCE)
        elif arguments.command == "lucky":
            query = " ".join(arguments.QUERY)
            qobuz.lucky_type = arguments.type
            qobuz.lucky_limit = arguments.number
            qobuz.lucky_mode(query)
        else:
            qobuz.interactive_limit = arguments.limit
            qobuz.interactive()

    except KeyboardInterrupt:
        logging.info(
            f"{RED}Interrupted by user\n{YELLOW}Already downloaded items will "
            "be skipped if you try to download the same releases again."
        )

    finally:
        _remove_leftovers(qobuz.directory)


def _initial_checks():
    if not os.path.isdir(CONFIG_PATH) or not os.path.isfile(CONFIG_FILE):
        os.makedirs(CONFIG_PATH, exist_ok=True)
        _reset_config(CONFIG_FILE)


def main():
    _initial_checks()

    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)

    try:
        email = config["DEFAULT"]["email"]
        password = config["DEFAULT"]["password"]
        default_folder = config["DEFAULT"]["default_folder"]
        default_limit = config["DEFAULT"]["default_limit"]
        default_quality = config["DEFAULT"]["default_quality"]
        default_start_mode = config["DEFAULT"]["default_start_mode"]
        default_mode_active = False
        no_m3u = config.getboolean("DEFAULT", "no_m3u")
        albums_only = config.getboolean("DEFAULT", "albums_only")
        no_fallback = config.getboolean("DEFAULT", "no_fallback")
        og_cover = config.getboolean("DEFAULT", "og_cover")
        embed_art = config.getboolean("DEFAULT", "embed_art")
        no_cover = config.getboolean("DEFAULT", "no_cover")
        no_database = config.getboolean("DEFAULT", "no_database")
        app_id = config["DEFAULT"]["app_id"]
        smart_discography = config.getboolean("DEFAULT", "smart_discography")
        
        # Get folder_format and track_format if they exist, otherwise use None
        # This allows them to be commented out in config.ini when using format_config.ini
        try:
            folder_format = config["DEFAULT"]["folder_format"]
            track_format = config["DEFAULT"]["track_format"]
        except KeyError:
            # Will use defaults from core.py when None
            folder_format = None
            track_format = None
        
        # Get naming mode settings
        try:
            default_naming_mode = config["DEFAULT"]["default_naming_mode"]
            current_naming_mode = config["DEFAULT"]["current_naming_mode"]
            dynamic_naming_mode = config.getboolean("DEFAULT", "dynamic_naming_mode")
        except (KeyError, ValueError):
            default_naming_mode = "artist_discography_dg"
            current_naming_mode = "artist_discography_dg"
            dynamic_naming_mode = True

        secrets = [
            secret for secret in config["DEFAULT"]["secrets"].split(",") if secret
        ]
        parser = qdl_args(default_quality, default_limit, default_folder)
        
        # Use default_start_mode if no arguments provided and mode is not 'none'
        if len(sys.argv) < 2 and default_start_mode.lower() != 'none':
            valid_modes = ['fun', 'dl', 'lucky']
            if default_start_mode.lower() in valid_modes:
                default_mode_active = True
                sys.argv.append(default_start_mode.lower())
                if default_start_mode.lower() == 'lucky':
                    # Lucky mode requires a query parameter
                    sys.argv.append('default_search')
                logging.info(f"{YELLOW}Using default start mode: {default_start_mode}")
            else:
                logging.warning(f"{YELLOW}Invalid default_start_mode in config: {default_start_mode}. Valid options are 'none', 'fun', 'dl', or 'lucky'.")
        
        # If still no command provided, print help
        if len(sys.argv) < 2:
            sys.exit(parser.print_help())
            
        arguments = parser.parse_args()
    except (KeyError, UnicodeDecodeError, configparser.Error) as error:
        arguments = qdl_args().parse_args()
        if not arguments.reset:
            sys.exit(
                f"{RED}Your config file is corrupted: {error}! "
                "Run 'qdl -r' to fix this."
            )

    if arguments.reset:
        sys.exit(_reset_config(CONFIG_FILE))

    if arguments.show_config:
        print(f"Configuation: {CONFIG_FILE}\nDatabase: {QOBUZ_DB}\n---")
        with open(CONFIG_FILE, "r") as f:
            print(f.read())
        sys.exit()

    if arguments.purge:
        try:
            os.remove(QOBUZ_DB)
        except FileNotFoundError:
            pass
        sys.exit(f"{GREEN}The database was deleted.")

    qobuz = QobuzDL(
        arguments.directory,
        arguments.quality,
        arguments.embed_art or embed_art,
        ignore_singles_eps=arguments.albums_only or albums_only,
        no_m3u_for_playlists=arguments.no_m3u or no_m3u,
        quality_fallback=not arguments.no_fallback or not no_fallback,
        cover_og_quality=arguments.og_cover or og_cover,
        no_cover=arguments.no_cover or no_cover,
        downloads_db=None if no_database or arguments.no_db else QOBUZ_DB,
        folder_format=arguments.folder_format or folder_format,
        track_format=arguments.track_format or track_format,
        smart_discography=arguments.smart_discography or smart_discography,
        default_naming_mode=default_naming_mode,
        current_naming_mode=current_naming_mode,
        dynamic_naming_mode=dynamic_naming_mode,
    )
    qobuz.initialize_client(email, password, app_id, secrets)

    _handle_commands(qobuz, arguments)


if __name__ == "__main__":
    sys.exit(main())
