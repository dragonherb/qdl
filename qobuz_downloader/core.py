import logging
import os
import sys
import time
import random
import configparser

import requests
from bs4 import BeautifulSoup as bso
from pathvalidate import sanitize_filename
from googlesearch import search as google_search
try:
    from pick import pick
except (ImportError, ModuleNotFoundError):
    if os.name == "nt":
        print("Please install curses with 'pip3 install windows-curses' to continue")
    # Don't exit here so other commands still work

from qobuz_downloader.bundle import Bundle
from qobuz_downloader import downloader, qopy
from qobuz_downloader.color import CYAN, OFF, RED, YELLOW, DF, RESET, GREEN, WHITE
from qobuz_downloader.exceptions import NonStreamable
from qobuz_downloader.db import create_db, handle_download_id
from qobuz_downloader.utils import (
    get_url_info,
    make_m3u,
    smart_discography_filter,
    format_duration,
    create_and_return_dir,
    PartialFormatter,
)

WEB_URL = "https://play.qobuz.com/"
ARTISTS_SELECTOR = "td.chartlist-artist > a"
TITLE_SELECTOR = "td.chartlist-name > a"
QUALITIES = {
    5: "5 - MP3",
    6: "6 - 16 bit, 44.1kHz",
    7: "7 - 24 bit, <96kHz",
    27: "27 - 24 bit, >96kHz",
}

logger = logging.getLogger(__name__)


class QobuzDL:
    def __init__(
        self,
        directory="QDL Downloads",
        quality=6,
        embed_art=False,
        lucky_limit=1,
        lucky_type="album",
        interactive_limit=20,
        ignore_singles_eps=False,
        no_m3u_for_playlists=False,
        quality_fallback=True,
        cover_og_quality=False,
        no_cover=False,
        downloads_db=None,
        folder_format="{artist} - {album} ({year}) [{bit_depth}B-{sampling_rate}kHz]",
        track_format="{tracknumber}. {artist} - {tracktitle}",
        smart_discography=False,
        default_naming_mode="artist_discography_dg",
        current_naming_mode="artist_discography_dg",
        dynamic_naming_mode=True,
    ):
        self.directory = create_and_return_dir(directory)
        self.quality = quality
        self.embed_art = embed_art
        self.lucky_limit = lucky_limit
        self.lucky_type = lucky_type
        self.interactive_limit = interactive_limit
        self.ignore_singles_eps = ignore_singles_eps
        self.no_m3u_for_playlists = no_m3u_for_playlists
        self.quality_fallback = quality_fallback
        self.cover_og_quality = cover_og_quality
        self.no_cover = no_cover
        self.downloads_db = create_db(downloads_db) if downloads_db else None
        self.folder_format = folder_format
        self.track_format = track_format
        self.smart_discography = smart_discography
        self.default_naming_mode = default_naming_mode
        self.current_naming_mode = current_naming_mode
        self.dynamic_naming_mode = dynamic_naming_mode
        
        # Initialize a flag to prevent duplicate format display
        self._from_dl_mode = False
        
        # Read format configuration
        self.format_config = configparser.ConfigParser()
        
        # Define user config path - this should be the primary config location
        user_config_path = os.path.join(os.environ.get('APPDATA', ''), 'QDL', 'format_config.ini') if os.name == 'nt' else os.path.join(os.environ.get('HOME', ''), '.config', 'QDL', 'format_config.ini')
        
        # Define fallback paths
        fallback_paths = [
            'format_config.ini',  # Current directory
            os.path.join(os.path.dirname(__file__), 'format_config.ini'),  # Module directory
        ]
        
        # First check if user config exists
        if os.path.exists(user_config_path):
            # User config exists, use it exclusively
            logger.info(f"Using user config file: {user_config_path}")
            self.format_config.read(user_config_path)
        else:
            # User config doesn't exist, try fallbacks
            logger.warning(f"User config file not found at {user_config_path}")
            logger.info("Checking fallback locations...")
            
            found_fallback = False
            for path in fallback_paths:
                if os.path.exists(path):
                    logger.info(f"Using fallback config from: {path}")
                    self.format_config.read(path)
                    found_fallback = True
                    break  # Only use the first fallback found
            
            if not found_fallback:
                logger.warning("No format_config.ini files found in any location!")

    def get_naming_mode(self, search_mode):
        """Get the naming mode based on search mode and configuration"""
        if self.dynamic_naming_mode:
            # Check if we have a search mode alias defined
            try:
                # Look for an alias section in format_config.ini
                alias_key = f"{search_mode}_search_mode"
                
                # First check if the exact search_mode has a mapping
                if search_mode in self.format_config:
                    return search_mode
                    
                # Check if there's a direct search_mode alias
                if alias_key in self.format_config['DEFAULT']:
                    return self.format_config['DEFAULT'][alias_key]
                    
                # Try to match by searching through all keys in format_config.ini
                for section in self.format_config.sections():
                    # Skip the actual formatting sections
                    if section.endswith('_search_mode'):
                        continue
                        
                    # Check if any key contains our search mode as value
                    for key, value in self.format_config.items(section):
                        if key.endswith('_search_mode') and search_mode.lower() in key.lower():
                            return value
                            
                # Check standard URL type mappings as fallback
                mode_mappings = {
                    'artist': 'artist_discography_dg',
                    'album': 'artist_album_release',
                    'label': 'label_discography_lpk',
                    'playlist': 'playlists_pls',
                    'track': 'single_track_trk'
                }
                
                if search_mode in mode_mappings:
                    return mode_mappings[search_mode]
            except (KeyError, configparser.Error) as e:
                logger.debug(f"Error mapping search mode alias: {e}. Using search_mode directly.")
            
            # If all else fails, just use the search_mode directly
            return search_mode
        
        # When dynamic naming is disabled, just use the current_naming_mode
        return self.current_naming_mode

    def format_folder_name(self, album_data, search_mode):
        """Format the folder name based on the naming mode"""
        try:
            naming_mode = self.get_naming_mode(search_mode)
            # Ensure the naming mode exists in the config
            if naming_mode not in self.format_config:
                naming_mode = self.default_naming_mode
                
            format_config = self.format_config[naming_mode]
            
            # Prepare variables for folder format
            variables = {
                'artist': album_data.get('artist', ''),
                'year': album_data.get('year', ''),
                'album': album_data.get('album', ''),
                'bit_depth': album_data.get('bit_depth', ''),
                'sampling_rate': album_data.get('sampling_rate', ''),
                'label': album_data.get('label', '') or album_data.get('name', ''),
                'playlist': album_data.get('playlist', '') or album_data.get('name', ''),
                'query': album_data.get('query', '')
            }
            
            # Check for the explicit is_root_folder flag from handle_url
            is_root_folder = album_data.get('is_root_folder', False)
            
            # Backup detection based on data structure and mode
            if not is_root_folder and (search_mode == 'label_discography_lpk' or search_mode == 'artist_discography_dg') and not album_data.get('album'):
                is_root_folder = True
                
            # Check if we should create a top folder for this mode
            create_top_folder = False
            try:
                # Default to False if not specified
                create_top_folder = format_config.getboolean('create_top_folder', False)
            except (configparser.Error, ValueError):
                # If there's an error parsing, default to mode-specific behavior
                if search_mode == 'label' or search_mode == 'label_discography_lpk' or search_mode == 'artist' or search_mode == 'artist_discography_dg':
                    create_top_folder = True
            
            # For root folders in label or artist collections, but only if create_top_folder is True
            if is_root_folder and create_top_folder:
                # First try to use the explicitly defined top_folder_format if available
                if 'top_folder_format' in format_config:
                    try:
                        folder_name = format_config['top_folder_format'].format(**variables)
                    except KeyError as e:
                        logger.warning(f"Error formatting top folder: {e}. Using fallback format.")
                        # Fallback based on search mode
                        if search_mode == 'label' or search_mode == 'label_discography_lpk':
                            folder_name = f"Label - {variables['label']}" if variables['label'] else album_data.get('name', 'Unknown Label')
                        else:
                            folder_name = variables['artist'] if variables['artist'] else album_data.get('name', 'Unknown Artist')
                else:
                    # No top_folder_format defined, use simple naming for root folders
                    if search_mode == 'label' or search_mode == 'label_discography_lpk':
                        folder_name = f"Label - {variables['label']}" if variables['label'] else album_data.get('name', 'Unknown Label')
                    else:
                        folder_name = variables['artist'] if variables['artist'] else album_data.get('name', 'Unknown Artist')
            elif is_root_folder and not create_top_folder:
                # If it's a root folder but we shouldn't create a top folder,
                # just return the base directory
                logger.info(f"Not creating top folder for {search_mode} as specified in configuration.")
                return self.directory
            else:
                # Regular album folder - use the standard folder format
                try:
                    folder_name = format_config['folder_format'].format(**variables)
                except KeyError as e:
                    logger.warning(f"Error formatting folder: {e}. Using album name.")
                    folder_name = album_data.get('album', album_data.get('name', 'Unknown'))
            
            folder_path = os.path.join(self.directory, sanitize_filename(folder_name))
            return create_and_return_dir(folder_path)
        except (KeyError, configparser.Error) as e:
            logger.warning(f"Error in format_folder_name: {e}. Using default folder format.")
            # Fallback to default format
            folder_name = sanitize_filename(album_data.get('name', 'Unknown'))
            return create_and_return_dir(os.path.join(self.directory, folder_name))

    def format_track_name(self, track_data, album_data, search_mode):
        """Format the track name based on the naming mode"""
        try:
            naming_mode = self.get_naming_mode(search_mode)
            # Ensure the naming mode exists in the config
            if naming_mode not in self.format_config:
                naming_mode = self.default_naming_mode
                
            format_config = self.format_config[naming_mode]
            
            # Prepare variables for track format
            variables = {
                'tracknumber': str(track_data.get('track_number', '')),
                'tracktitle': track_data.get('title', ''),
                'artist': album_data.get('artist', ''),
                'track_number': str(track_data.get('track_number', '')),  # Alternate name
                'track_title': track_data.get('title', '')  # Alternate name
            }
            
            # Get the track format string
            track_format = format_config['track_format']
            
            # Check if the track_format is referencing default_track_format
            if track_format == 'default_track_format' and 'default_track_format' in self.format_config['DEFAULT']:
                track_format = self.format_config['DEFAULT']['default_track_format']
            
            # Format the track name
            return track_format.format(**variables)
        except (KeyError, configparser.Error) as e:
            logger.warning(f"Error in format_track_name: {e}. Using default track format.")
            # Fallback to default format
            return f"{track_data.get('track_number', '')}. {track_data.get('title', 'Unknown')}"

    def initialize_client(self, email, pwd, app_id, secrets):
        self.client = qopy.Client(email, pwd, app_id, secrets)
        logger.info(f"{WHITE}Set max quality: {QUALITIES[int(self.quality)]}\n")

    def get_tokens(self):
        bundle = Bundle()
        self.app_id = bundle.get_app_id()
        self.secrets = [
            secret for secret in bundle.get_secrets().values() if secret
        ]  # avoid empty fields

    def download_from_id(self, item_data, album=True, alt_path=None, from_dl_mode=False):
        # Handle both old-style item_id (string) and new-style item_meta (dict)
        if isinstance(item_data, dict):
            item_id = item_data.get("id")
            parent_search_mode = item_data.get("parent_search_mode")
            # Check if this is coming from dl mode
            from_dl_mode = item_data.get("from_dl_mode", False)
        else:
            item_id = item_data
            parent_search_mode = None
            
        if handle_download_id(self.downloads_db, item_id, add_id=False):
            logger.info(
                f"{OFF}This release ID ({item_id}) was already downloaded "
                "according to the local database.\nUse the '--no-db' flag "
                "to bypass this."
            )
            return
        try:
            # Get the proper folder and track formats
            folder_format = self.folder_format
            track_format = self.track_format
            
            # Determine the naming mode to use
            naming_mode = None
            if parent_search_mode:
                # If we have a parent search mode, use that
                naming_mode = self.get_naming_mode(parent_search_mode)
            else:
                # For direct album/track downloads, use the appropriate mode based on the album flag
                naming_mode = self.get_naming_mode("album" if album else "track")
            
            # Get format settings from the config if the naming mode exists
            if naming_mode in self.format_config:
                format_config = self.format_config[naming_mode]
                folder_format = format_config.get('folder_format', self.folder_format)
                track_format = format_config.get('track_format', self.track_format)
                
                # Always check if the track_format is referencing default_track_format
                if track_format == 'default_track_format' and 'default_track_format' in self.format_config['DEFAULT']:
                    track_format = self.format_config['DEFAULT']['default_track_format']
                    
                # Only display format info if not coming from dl mode (where it's already shown)
                # Check both the from_dl_mode parameter and the class attribute
                if not from_dl_mode and not getattr(self, '_from_dl_mode', False):
                    logger.info(f"{YELLOW}Track Format:{WHITE} {track_format} {GREEN}(Default){RESET}")
                    logger.info(f"{YELLOW}Folder Format:{WHITE} {folder_format} {GREEN}({naming_mode.replace('_', ' ').title()}){RESET}")
                    
            dloader = downloader.Download(
                self.client,
                item_id,
                alt_path or self.directory,
                int(self.quality),
                self.embed_art,
                self.ignore_singles_eps,
                self.quality_fallback,
                self.cover_og_quality,
                self.no_cover,
                folder_format,
                track_format,
            )
            dloader.download_id_by_type(not album)
            handle_download_id(self.downloads_db, item_id, add_id=True)
        except (requests.exceptions.RequestException, NonStreamable) as e:
            logger.error(f"{RED}Error getting release: {e}. Skipping...")

    def handle_url(self, url):
        possibles = {
            "playlist": {
                "func": self.client.get_plist_meta,
                "iterable_key": "tracks",
            },
            "artist": {
                "func": self.client.get_artist_meta,
                "iterable_key": "albums",
            },
            "label": {
                "func": self.client.get_label_meta,
                "iterable_key": "albums",
            },
            "album": {"album": True, "func": None, "iterable_key": None},
            "track": {"album": False, "func": None, "iterable_key": None},
        }
        try:
            url_type, item_id = get_url_info(url)
            type_dict = possibles[url_type]
        except (KeyError, IndexError):
            logger.info(
                f'{RED}Invalid url: "{url}". Use urls from ' "https://play.qobuz.com!"
            )
            return
        if type_dict["func"]:
            content = [item for item in type_dict["func"](item_id)]
            content_name = content[0]["name"]
            logger.info(
                f"{GREEN}Downloading all the music from {content_name} "
                f"({url_type})!"
            )
            
            # Prepare the root folder data with proper naming values
            root_folder_data = content[0].copy()
            
            # For label collections - ensure we have a proper label name
            if url_type == "label":
                # Add the label name to ensure it can be used in top_folder_format
                root_folder_data["label"] = content_name
                # Set special flag for root folders
                root_folder_data["is_root_folder"] = True
                
            # For artist collections - ensure we have a proper artist name
            elif url_type == "artist":
                # Make sure artist name is available
                root_folder_data["artist"] = content_name
                # Set special flag for root folders
                root_folder_data["is_root_folder"] = True
                
            # For playlist collections - ensure we have proper playlist name
            elif url_type == "playlist":
                # Make sure artist (creator) is available
                root_folder_data["artist"] = content_name
                root_folder_data["playlist"] = content_name
                # Set special flag for root folders
                root_folder_data["is_root_folder"] = True
            
            new_path = self.format_folder_name(root_folder_data, url_type)

            if self.smart_discography and url_type == "artist":
                items = smart_discography_filter(
                    content,
                    save_space=True,
                    skip_extras=True,
                )
            else:
                items = [item[type_dict["iterable_key"]]["items"] for item in content][
                    0
                ]

            logger.info(f"{GREEN}{len(items)} downloads in queue")
            for item in items:
                # Pass the parent search mode to ensure child folders use the correct format
                # Create a dictionary to store the search mode for download_from_id
                item_meta = {
                    "id": item["id"],
                    "parent_search_mode": url_type,
                    "from_dl_mode": False  # This is from fun mode, not dl mode
                }
                self.download_from_id(
                    item_meta,
                    True if type_dict["iterable_key"] == "albums" else False,
                    new_path,
                )
            if url_type == "playlist" and not self.no_m3u_for_playlists:
                make_m3u(new_path)
        else:
            self.download_from_id(item_id, type_dict["album"])

    def download_list_of_urls(self, urls):
        if not urls or not isinstance(urls, list):
            logger.info(f"{OFF}Nothing to download")
            return
        for url in urls:
            if "last.fm" in url:
                self.download_lastfm_pl(url)
            elif os.path.isfile(url):
                self.download_from_txt_file(url)
            else:
                self.handle_url(url)
                
    def direct_link_menu(self, urls):
        """Display a menu for selecting download type for direct links and then download them."""
        if not urls or not isinstance(urls, list):
            logger.info(f"{OFF}Nothing to download")
            return
            
        try:
            from pick import pick
        except (ImportError, ModuleNotFoundError):
            if os.name == "nt":
                sys.exit(
                    "Please install curses with "
                    '"pip3 install windows-curses" to continue'
                )
            raise
            
        # Define the download types
        download_types = ["Artist", "Album", "Track", "Playlist", "LabelPack"]
        
        try:
            # Create custom picker for the download type selection with consistent styling
            import curses
            from pick import Picker
            
            # Custom render function for the menu selection
            def custom_menu_render(screen, options, selected_option_index, title):
                try:
                    # Setup colors
                    curses.start_color()
                    curses.use_default_colors()
                    curses.init_pair(1, curses.COLOR_GREEN, -1)  # Green text on default background (-1)
                    curses.init_pair(2, curses.COLOR_YELLOW, -1)  # Yellow text on default background (-1)
                    
                    GREEN = curses.color_pair(1)
                    YELLOW = curses.color_pair(2)
                    
                    # Clear screen
                    screen.clear()
                    
                    # Draw title
                    screen.addstr(0, 0, title)
                    screen.addstr(title.count('\n') + 1, 0, "")
                    
                    # Draw options
                    for i, option in enumerate(options):
                        line_position = title.count('\n') + 2 + i
                        
                        # Add prefix based on whether this is the selected option
                        if i == selected_option_index:
                            # Draw yellow asterisk and green text for selected option
                            screen.addstr(line_position, 0, " ")
                            screen.addstr("*", YELLOW)
                            screen.addstr(" ")
                            screen.addstr(option, GREEN)
                        else:
                            screen.addstr(line_position, 0, "  " + option)
                    
                    screen.refresh()
                
                except Exception as e:
                    # In case of any error, fall back to normal pick
                    pass
            
            # Create custom picker for menu selection
            menu_picker = Picker(download_types, "Specify the type of download link, please:\n[press Intro]")
            
            # Override the draw method
            menu_picker.draw = lambda screen: custom_menu_render(screen, download_types, menu_picker.index, "Specify the type of download link, please:\n[press Intro]")
            
            # Start the picker
            selected_type, _ = menu_picker.start()
            
            # Convert selected_type to string to ensure it works as a dictionary key
            selected_type_str = str(selected_type)
            
            # Map the selected type to the URL type expected by handle_url
            type_mapping = {
                "Artist": "artist",
                "Album": "album",
                "Track": "track",
                "Playlist": "playlist",
                "LabelPack": "label"
            }
            
            # Get the URL type from the user's selection
            selected_url_type = type_mapping.get(selected_type_str, "album")  # Default to album if not found
            
            # Get the naming mode based on the selected type
            naming_mode = self.get_naming_mode(selected_url_type)
            
            # Get the format settings from the config
            try:
                if naming_mode in self.format_config:
                    format_config = self.format_config[naming_mode]
                    folder_format = format_config.get('folder_format', self.folder_format)
                    track_format = format_config.get('track_format', self.track_format)
                    
                    # Check if track_format references default_track_format
                    if track_format == 'default_track_format' and 'default_track_format' in self.format_config['DEFAULT']:
                        track_format = self.format_config['DEFAULT']['default_track_format']
                else:
                    folder_format = self.folder_format
                    track_format = self.track_format
            except (KeyError, configparser.Error):
                folder_format = self.folder_format
                track_format = self.track_format
            
            # Display the enhanced output message with the requested formatting
            logger.info(f"{GREEN}Processing URLs as {selected_url_type} type, using:{RESET}")
            logger.info(f"{YELLOW}Track Format:{WHITE} {track_format} {GREEN}(Default){RESET}")
            logger.info(f"{YELLOW}Folder Format:{WHITE} {folder_format} {GREEN}({naming_mode.replace('_', ' ').title()}){RESET}")
            
            # Process each URL with the selected type
            for url in urls:
                if "last.fm" in url:
                    self.download_lastfm_pl(url)
                elif os.path.isfile(url):
                    self.download_from_txt_file(url)
                else:
                    try:
                        # Extract the URL info
                        _, item_id = get_url_info(url)
                        
                        # Create a new URL with the selected type and the extracted ID
                        # This is a bit of a hack, but it allows us to reuse the handle_url function
                        if "qobuz.com" in url:
                            # For Qobuz URLs, we can modify the URL type
                            parts = url.split("/")
                            for i, part in enumerate(parts):
                                if part in ["album", "track", "artist", "label", "playlist"]:
                                    parts[i] = selected_url_type
                                    break
                            new_url = "/".join(parts)
                        else:
                            # For other URLs, construct a fake URL with the selected type
                            new_url = f"https://play.qobuz.com/{selected_url_type}/{item_id}"
                        
                        # Mark this as coming from dl mode
                        if selected_url_type == "album" or selected_url_type == "track":
                            # For direct album/track downloads, we need to extract the ID and call download_from_id
                            try:
                                _, item_id = get_url_info(new_url)
                                item_meta = {
                                    "id": item_id,
                                    "parent_search_mode": selected_url_type,
                                    "from_dl_mode": True  # Mark as coming from dl mode
                                }
                                self.download_from_id(
                                    item_meta,
                                    selected_url_type == "album",
                                    None,
                                    True
                                )
                            except Exception as e:
                                logger.error(f"{RED}Error processing URL: {e}. Using handle_url.")
                                self.handle_url(new_url)
                        else:
                            # For other types (artist, label, playlist), set a flag to prevent duplicate format display
                            self._from_dl_mode = True
                            try:
                                # Use handle_url with the flag set
                                self.handle_url(new_url)
                            finally:
                                # Reset the flag after handle_url completes
                                self._from_dl_mode = False
                        
                    except (KeyError, IndexError) as e:
                        logger.info(f'{RED}Error processing URL: {e}. Using original URL.')
                        self.handle_url(url)
                
        except Exception as e:
            logger.error(f"{RED}Error in direct link menu: {e}. Proceeding with default handling.")
            # Fall back to normal download_list_of_urls
            self.download_list_of_urls(urls)

    def download_from_txt_file(self, txt_file):
        with open(txt_file, "r") as txt:
            try:
                urls = [
                    line.replace("\n", "")
                    for line in txt.readlines()
                    if not line.strip().startswith("#")
                ]
            except Exception as e:
                logger.error(f"{RED}Invalid text file: {e}")
                return
            logger.info(
                f"{YELLOW}qdl will download {len(urls)}"
                f" urls from file: {txt_file}"
            )
            self.download_list_of_urls(urls)

    def lucky_mode(self, query, download=True):
        if len(query) < 3:
            logger.info(f"{RED}Your search query is too short or invalid")
            return

        logger.info(
            f'{YELLOW}Searching {self.lucky_type}s for "{query}".\n'
            f"{YELLOW}qdl will attempt to download the first "
            f"{self.lucky_limit} results."
        )
        results = self.search_by_type(query, self.lucky_type, self.lucky_limit, True)

        if download:
            self.download_list_of_urls(results)

        return results

    def search_by_type(self, query, item_type, limit=10, lucky=False):
        if len(query) < 3:
            logger.info("{RED}Your search query is too short or invalid")
            return

        possibles = {
            "album": {
                "func": self.client.search_albums,
                "album": True,
                "key": "albums",
                "format": "{artist[name]} - {title}",
                "requires_extra": True,
            },
            "artist": {
                "func": self.client.search_artists,
                "album": True,
                "key": "artists",
                "format": "{name} - ({albums_count} releases)",
                "requires_extra": False,
            },
            "track": {
                "func": self.client.search_tracks,
                "album": False,
                "key": "tracks",
                "format": "{performer[name]} - {title}",
                "requires_extra": True,
            },
            "playlist": {
                "func": self.client.search_playlists,
                "album": False,
                "key": "playlists",
                "format": "{name} - ({tracks_count} releases)",
                "requires_extra": False,
            },
        }

        try:
            mode_dict = possibles[item_type]
            results = mode_dict["func"](query, limit)
            iterable = results[mode_dict["key"]]["items"]
            item_list = []
            for i in iterable:
                fmt = PartialFormatter()
                text = fmt.format(mode_dict["format"], **i)
                if mode_dict["requires_extra"]:

                    text = "{} - {} [{}]".format(
                        text,
                        format_duration(i["duration"]),
                        "HI-RES" if i["hires_streamable"] else "LOSSLESS",
                    )

                url = "{}{}/{}".format(WEB_URL, item_type, i.get("id", ""))
                item_list.append({"text": text, "url": url} if not lucky else url)
            return item_list
        except (KeyError, IndexError):
            logger.info(f"{RED}Invalid type: {item_type}")
            return

    def download_lastfm_pl(self, playlist_url):
        # Apparently, last fm API doesn't have a playlist endpoint. If you
        # find out that it has, please fix this!
        try:
            r = requests.get(playlist_url, timeout=10)
        except requests.exceptions.RequestException as e:
            logger.error(f"{RED}Playlist download failed: {e}")
            return
        soup = bso(r.content, "html.parser")
        artists = [artist.text for artist in soup.select(ARTISTS_SELECTOR)]
        titles = [title.text for title in soup.select(TITLE_SELECTOR)]

        track_list = []
        if len(artists) == len(titles) and artists:
            track_list = [
                artist + " " + title for artist, title in zip(artists, titles)
            ]

        if not track_list:
            logger.info(f"{OFF}Nothing found")
            return

        pl_title = sanitize_filename(soup.select_one("h1").text)
        pl_directory = os.path.join(self.directory, pl_title)
        logger.info(
            f"{YELLOW}Downloading playlist: {pl_title} " f"({len(track_list)} tracks)"
        )

        for i in track_list:
            track_id = get_url_info(self.search_by_type(i, "track", 1, lucky=True)[0])[
                1
            ]
            if track_id:
                self.download_from_id(track_id, False, pl_directory)

        if not self.no_m3u_for_playlists:
            make_m3u(pl_directory)

    def interactive(self, download=True):
        """Interactive mode to search and download from Qobuz."""
        try:
            from pick import pick
        except (ImportError, ModuleNotFoundError):
            if os.name == "nt":
                sys.exit(
                    "Please install curses with "
                    '"pip3 install windows-curses" to continue'
                )
            raise

        def get_quality_text(option):
            opt_dict = QUALITIES
            return "{0}".format(opt_dict[option["q"]])

        qualities = [{"q": q} for q in QUALITIES.keys()]
        
        # Get the index of the currently configured quality setting
        current_quality_index = 0
        for i, q in enumerate(qualities):
            if q["q"] == int(self.quality):
                current_quality_index = i
                break
        
        def get_title_text(option):
            # Simple function to return just the text without any decorations
            # Our custom drawing function will handle the colors
            return option.get("text")

        try:
            item_types = ["Artists", "Albums", "Tracks", "Playlists", "Label search (Google)"]
            
            # Create custom picker for the first menu with consistent styling
            import curses
            from pick import Picker
            
            # Custom render function for the initial menu selection
            def custom_menu_render(screen, options, selected_option_index, title):
                try:
                    # Setup colors
                    curses.start_color()
                    curses.use_default_colors()
                    curses.init_pair(1, curses.COLOR_GREEN, -1)  # Green text on default background (-1)
                    curses.init_pair(2, curses.COLOR_YELLOW, -1)  # Yellow text on default background (-1)
                    
                    GREEN = curses.color_pair(1)
                    YELLOW = curses.color_pair(2)
                    
                    # Clear screen
                    screen.clear()
                    
                    # Draw title
                    screen.addstr(0, 0, title)
                    screen.addstr(title.count('\n') + 1, 0, "")
                    
                    # Draw options
                    for i, option in enumerate(options):
                        line_position = title.count('\n') + 2 + i
                        
                        # Add prefix based on whether this is the selected option
                        if i == selected_option_index:
                            # Draw yellow asterisk and green text for selected option
                            screen.addstr(line_position, 0, " ")
                            screen.addstr("*", YELLOW)
                            screen.addstr(" ")
                            screen.addstr(option, GREEN)
                        else:
                            screen.addstr(line_position, 0, "  " + option)
                    
                    screen.refresh()
                
                except Exception as e:
                    # In case of any error, fall back to normal pick
                    pass
            
            # Create custom picker for menu selection
            menu_picker = Picker(item_types, "I'll search for:\n[press Intro]")
            
            # Override the draw method
            menu_picker.draw = lambda screen: custom_menu_render(screen, item_types, menu_picker.index, "I'll search for:\n[press Intro]")
            
            # Start the picker
            selected_type, _ = menu_picker.start()
            selected_type = selected_type[:-1].lower()
            
            # Handle special case for label search
            if selected_type == "label search (google":
                logger.info(f"{YELLOW}Ok, we'll search for labels using Google{RESET}")
                self.google_label_search()
                return
                
            logger.info(f"{YELLOW}Ok, we'll search for " f"{selected_type}s{RESET}")
            final_url_list = []
            while True:
                query = input(
                    f"{CYAN}Enter your search: [Ctrl + c to quit]\n" f"-{DF} "
                )
                logger.info(f"{YELLOW}Searching...{RESET}")
                options = self.search_by_type(
                    query, selected_type, self.interactive_limit
                )
                if not options:
                    logger.info(f"{OFF}Nothing found{RESET}")
                    continue
                # Create a dynamic title that updates with the selection count
                def get_dynamic_title(count=0):
                    return (
                        f'*** RESULTS FOR "{query.title()}" ***\n\n'
                        f"Selected: {count} items\n"
                        "Press [space] to select/unselect, [enter] to confirm selection\n"
                        "Press Ctrl+c to quit, or don't select anything to search again"
                    )
                
                # Create a customized version of the picker to provide real-time feedback
                try:
                    # We'll implement a modified approach using the existing pick library
                    from pick import Picker
                    import curses
                    
                    # Create a list to track selected items
                    selected_items = []
                    
                    # Override the get_option_lines method to use curses colors for selected items
                    def custom_get_option_lines(self):
                        lines = []
                        for index, option in enumerate(self.options):
                            # Add asterisk only for current cursor position, not for selected items
                            prefix = "* " if index == self.index else "  "
                            
                            # Check if this option is selected (for color, not for asterisk)
                            if option.get("selected", False):
                                # The curses library will use this special indicator to apply green color
                                line = f"\x01{self.options_map_func(option)}\x02"
                            else:
                                line = self.options_map_func(option)
                                
                            lines.append(f"{prefix}{line}")
                        return lines
                    
                    # Create handler functions for the picker
                    def handle_selection(picker):
                        # Get the currently highlighted option
                        current_index = picker.index
                        option = picker.options[current_index]
                        
                        # Toggle selection status
                        if option.get("selected", False):
                            # Remove selection
                            option["selected"] = False
                            if option in selected_items:
                                selected_items.remove(option)
                        else:
                            # Add selection
                            option["selected"] = True
                            if option not in selected_items:
                                selected_items.append(option)
                        
                        # Update the title with the current selection count
                        picker.title = get_dynamic_title(len(selected_items))
                        return None  # Continue selection
                    
                    # Custom _start method for the picker that sets up colors
                    def custom_start(screen):
                        # Setup curses colors
                        curses.start_color()  # Initialize color support
                        curses.use_default_colors()  # Use terminal's default colors
                        
                        # Define color pairs using proper curses color constants
                        curses.init_pair(1, curses.COLOR_GREEN, -1)  # Green text on default background
                        curses.init_pair(2, curses.COLOR_YELLOW, -1)  # Yellow text on default background
                        
                        # Set up screen for our custom display
                        screen.clear()
                        
                        # Call the original _start method (after patching)
                        return picker._original_start(screen)
                    
                    # Custom draw function to apply colors to selected items
                    def custom_draw(self, screen):
                        """Draw the picker to the screen"""
                        self.screen = screen
                        
                        try:
                            # Get terminal dimensions
                            max_y, max_x = screen.getmaxyx()
                            
                            # Setup colors
                            curses.start_color()
                            curses.use_default_colors()
                            curses.init_pair(1, curses.COLOR_GREEN, -1)  # Green text on default background
                            curses.init_pair(2, curses.COLOR_YELLOW, -1)  # Yellow text on default background
                            
                            GREEN = curses.color_pair(1)
                            YELLOW = curses.color_pair(2)
                            
                            # Draw the title (truncate if needed)
                            title_lines = self.title.split('\n')
                            for i, title_line in enumerate(title_lines):
                                if i < max_y:
                                    # Truncate title line if it's too long
                                    if len(title_line) > max_x - 1:
                                        title_line = title_line[:max_x - 4] + "..."
                                    screen.addstr(i, 0, title_line)
                            
                            # Add a blank line if there's room
                            if self.title.count('\n') + 1 < max_y:
                                screen.addstr(self.title.count('\n') + 1, 0, "")

                            # Print options
                            option_lines = self.get_option_lines()
                            for index, line in enumerate(option_lines):
                                line_position = self.title.count('\n') + 2 + index
                                
                                # Skip if we're past the bottom of the screen
                                if line_position >= max_y:
                                    break
                                
                                if "\x01" in line and "\x02" in line:  # Special color indicators for green text
                                    # Split line into parts before and after color indicators
                                    before, rest = line.split("\x01", 1)
                                    middle, after = rest.split("\x02", 1)
                                    
                                    # Truncate parts if combined length is too long
                                    total_length = len(before) + len(middle) + len(after)
                                    if total_length > max_x - 1:
                                        # Prioritize showing the beginning with some of the middle
                                        available_space = max_x - 1 - len(before) - 3  # -3 for "..."
                                        if available_space > 0:
                                            middle = middle[:available_space]
                                            after = "..."
                                        else:
                                            # If even the prefix is too long, truncate it
                                            before = before[:max_x - 4]
                                            middle = ""
                                            after = "..."
                                    
                                    # Print asterisk in yellow if it's in the prefix
                                    if "*" in before:
                                        asterisk_pos = before.find("*")
                                        screen.addstr(line_position, 0, before[:asterisk_pos])
                                        screen.addstr("*", YELLOW)  # Yellow asterisk
                                        screen.addstr(before[asterisk_pos+1:])
                                    else:
                                        screen.addstr(line_position, 0, before)
                                        
                                    # Print the selected text in green
                                    screen.addstr(middle, GREEN)  # Apply green color
                                    screen.addstr(after)
                                else:
                                    # Regular line without color highlights for selection
                                    # Truncate if too long
                                    if len(line) > max_x - 1:
                                        line = line[:max_x - 4] + "..."
                                    
                                    # But still highlight the asterisk in yellow if present
                                    if "*" in line:
                                        asterisk_pos = line.find("*")
                                        screen.addstr(line_position, 0, line[:asterisk_pos])
                                        screen.addstr("*", YELLOW)  # Yellow asterisk
                                        screen.addstr(line[asterisk_pos+1:])
                                    else:
                                        screen.addstr(line_position, 0, line)
                            
                            # Move cursor to selected option if it's visible
                            cursor_position = self.title.count('\n') + 2 + self.index
                            if cursor_position < max_y:
                                screen.move(cursor_position, 0)
                            # Refresh the screen
                            screen.refresh()
                            
                        except Exception as e:
                            # Handle any errors gracefully
                            # Clear screen and show error message
                            screen.clear()
                            error_msg = f"Display error: {str(e)[:max_x-20]}..."
                            screen.addstr(0, 0, error_msg[:max_x-1])
                            screen.refresh()
                        
                    # Create a custom picker with our handler
                    picker = Picker(options, get_dynamic_title(0), options_map_func=get_title_text)
                    
                    # Save original methods before overriding
                    picker._original_start = picker._start
                    
                    # Override methods to use our custom implementations
                    picker.get_option_lines = lambda: custom_get_option_lines(picker)
                    picker._start = custom_start
                    picker.draw = lambda screen: custom_draw(picker, screen)
                    
                    # Register the spacebar key to toggle selection
                    picker.register_custom_handler(ord(' '), handle_selection)
                    
                    # Start the picker with curses wrapper
                    option, index = picker.start()
                    
                    # At this point, the user has pressed Enter to finish selection
                    # Our selected_items list contains all items that were toggled on
                    
                except (ImportError, KeyboardInterrupt):
                    # If ctrl+c is pressed or there's an error with the picker
                    logger.info(f"{YELLOW}Selection cancelled.{RESET}")
                    return
                if len(selected_items) > 0:
                    [final_url_list.append(item["url"]) for item in selected_items]
                    y_n = pick(
                        ["Yes, start the download", "No, continue searching"],
                        f"{len(selected_items)} items were added to queue to be downloaded. "
                        "Proceed to download?",
                        default_index=0
                    )
                    if y_n[0][0] == "Y":
                        break
                else:
                    logger.info(f"{YELLOW}Ok, try again...{RESET}")
                    continue
            if final_url_list:
                desc = (
                    "Select [intro] the quality (the quality will "
                    "be automatically\ndowngraded if the selected "
                    "is not found)"
                )
                self.quality = pick(
                    qualities,
                    desc,
                    default_index=current_quality_index,
                    options_map_func=get_quality_text,
                )[0]["q"]

                if download:
                    self.download_list_of_urls(final_url_list)

                return final_url_list
        except KeyboardInterrupt:
            logger.info(f"{YELLOW}Bye")
            return

    def google_label_search(self):
        """Search for Qobuz labels using Google and download their content."""
        try:
            final_url_list = []
            
            # Define the get_title_text function locally
            def get_title_text(option):
                return option.get("text")
                
            while True:
                query = input(
                    f"{CYAN}Enter label name to search: [Ctrl + c to quit]\n" f"-{DF} "
                )
                logger.info(f"{WHITE}Searching for label: {query} on Google...{RESET}")
                
                # Perform Google search for Qobuz label using googlesearch-python
                label_urls = self.search_label_on_google(query)
                
                if not label_urls:
                    logger.info(f"{OFF}No label results found{RESET}")
                    continue
                
                # Format options for display
                options = []
                for i, url_info in enumerate(label_urls):
                    # Don't print original URLs for each result to reduce output verbosity
                    
                    # Extract just the essential parts for downloading
                    original_url = url_info['url']
                    clean_url = original_url  # Default to using the original URL
                    
                    # Fix lowercase vs uppercase differences and replace region code
                    if "download-streaming-albums/" in original_url.lower():
                        # Get the exact pattern that appears in the URL (could be different case)
                        pattern = ""
                        for segment in ["download-streaming-albums/", "Download-Streaming-Albums/", "download-streaming-albums/"]:
                            if segment in original_url:
                                pattern = segment
                                break
                        
                        # If pattern was found, remove it
                        if pattern:
                            clean_url = original_url.replace(pattern, "")
                    
                    # Also check for the space version
                    elif "Download Streaming Albums/" in original_url:
                        clean_url = original_url.replace("Download Streaming Albums/", "")
                    
                    # Always use se-en region code for label URLs since that has been confirmed to work
                    if "/label/" in clean_url:
                        # Find the region code in the URL
                        for region in ["/us-en/", "/ar-es/", "/gb-en/", "/fr-fr/", "/de-de/"]:
                            if region in clean_url:
                                # Replace with the working region code
                                clean_url = clean_url.replace(region, "/se-en/")
                                break
                    
                    # Show both original and cleaned URL in the menu for debugging
                    display_title = f"{i+1}. {url_info['title']} [{clean_url}]"
                    
                    options.append({
                        "text": display_title,
                        "url": clean_url,
                        "original_url": original_url  # Keep the original URL for reference
                    })
                
                title = f'*** LABEL RESULTS FOR "{query.title()}" ***\n\n'
                title += "(Use arrow keys to select, press Enter to download, or Ctrl+C to quit)\n"
                
                if options:
                    try:
                        # Using curses for consistent visual styling
                        import curses
                        
                        # Custom render function to ensure consistent styling with yellow asterisk and green selection
                        def custom_label_render(screen, options, selected_option_index, title):
                            try:
                                # Setup colors
                                curses.start_color()
                                curses.use_default_colors()
                                curses.init_pair(1, curses.COLOR_GREEN, -1)  # Green text on default background (-1)
                                curses.init_pair(2, curses.COLOR_YELLOW, -1)  # Yellow text on default background (-1)
                                
                                GREEN = curses.color_pair(1)
                                YELLOW = curses.color_pair(2)
                                
                                # Clear screen
                                screen.clear()
                                
                                # Draw title
                                screen.addstr(0, 0, title)
                                screen.addstr(title.count('\n') + 1, 0, "")
                                
                                # Draw options
                                for i, option in enumerate(options):
                                    text = get_title_text(option)
                                    line_position = title.count('\n') + 2 + i
                                    
                                    # Add prefix based on whether this is the selected option
                                    prefix = "* " if i == selected_option_index else "  "
                                    
                                    # Highlight current item in green (no multiselect in label search)
                                    if i == selected_option_index:
                                        # Draw yellow asterisk
                                        screen.addstr(line_position, 0, " ")
                                        screen.addstr("*", YELLOW)
                                        screen.addstr(" ")
                                        # Draw option text in green
                                        screen.addstr(text, GREEN)
                                    else:
                                        screen.addstr(line_position, 0, prefix + text)
                                
                                screen.refresh()
                            
                            except Exception as e:
                                # In case of any error, fall back to normal pick
                                pass
                        
                        # Import Picker class for more control
                        from pick import Picker
                        
                        # Create custom picker for label selection
                        label_picker = Picker(options, title, options_map_func=get_title_text)
                        
                        # Override the draw method
                        original_draw = label_picker.draw
                        label_picker.draw = lambda screen: custom_label_render(screen, options, label_picker.index, title)
                        
                        # Use the custom picker
                        selected = label_picker.start()
                        
                        # Use the cleaned URL for downloading
                        label_url = selected[0]["url"]
                        original_url = selected[0]["original_url"]
                        
                        # Add to the URL list and proceed directly to processing
                        final_url_list.append(label_url)
                        
                        # Show just one line about processing the URL
                        logger.info(f"{WHITE}Processing label URL: {label_url}{RESET}")
                        try:
                            self.handle_url(label_url)
                            break
                        except KeyboardInterrupt:
                            logger.info(f"{YELLOW}Download interrupted by user. Exiting...{RESET}")
                            sys.exit(1)
                    except KeyboardInterrupt:
                        logger.info(f"{YELLOW}Selection cancelled. Try again or press Ctrl+C again to exit...{RESET}")
                        # Check if user wants to exit completely
                        try:
                            time.sleep(0.5)  # Brief pause to catch a second Ctrl+C
                        except KeyboardInterrupt:
                            logger.info(f"{YELLOW}Exiting label search...{RESET}")
                            sys.exit(1)
                        continue
        except KeyboardInterrupt:
            logger.info(f"{YELLOW}Bye{RESET}")
            sys.exit(1)
            return

    def search_label_on_google(self, query):
        """Search for Qobuz labels on Google using googlesearch-python library."""
        try:
            # Try different search queries to maximize results
            search_queries = [
                f"site:qobuz.com {query} label",
                f"site:qobuz.com/label {query}",
                f"site:qobuz.com {query} record label",
                # Added more specific queries to try to catch additional labels
                f"\"{query}\" site:qobuz.com label",
                f"qobuz.com label \"{query}\""
            ]
            
            search_results = []
            seen_urls = set()  # Keep track of seen URLs to avoid duplicates
            
            # Try each search query
            for search_query in search_queries:
                logger.info(f"{WHITE}Searching with query: {search_query}{RESET}")
                
                try:
                    # Add pause and random delays to avoid rate limiting
                    time.sleep(random.uniform(1.0, 2.0))
                    
                    # Use googlesearch-python to find Qobuz URLs
                    # Increased num_results to find more potential matches
                    for url in google_search(search_query, num_results=10, lang="en"):
                        # Only print the first found URL for each search query, avoid flooding the output
                        if len(search_results) == 0 and "/label/" in url:
                            logger.info(f"{WHITE}Found URL: {url}{RESET}")
                        
                        # Skip if we've already seen this URL
                        if url in seen_urls:
                            continue
                        
                        seen_urls.add(url)  # Add to seen URLs
                        
                        # More flexible pattern matching for Qobuz label URLs
                        # Loosened criteria to catch more potential label URLs
                        if "qobuz.com" in url and ("/label/" in url or "label" in url.lower()):
                            # Extract label name for display
                            try:
                                if "/label/" in url:
                                    # Extract just the label name part for display
                                    label_name = url.split("/label/")[-1].split("/")[0].replace("-", " ").title()
                                else:
                                    label_name = query.title() + " (Qobuz)"
                                
                                if not label_name:
                                    label_name = query.title() + " (Qobuz Label)"
                                
                                search_results.append({
                                    "title": label_name,
                                    "url": url
                                })
                            except Exception as e:
                                logger.info(f"{YELLOW}Error extracting label name: {str(e)}{RESET}")
                                # Still add the result even if name extraction fails
                                search_results.append({
                                    "title": f"{query.title()} (Qobuz)",
                                    "url": url
                                })
                except Exception as e:
                    logger.info(f"{YELLOW}Error in search query: {str(e)}{RESET}")
                    continue
                
                # Continue trying all queries to get more comprehensive results
                # (Previously, the code would break after finding at least one result)
                # Note: Removed the early break to collect results from all search queries
            
            # If still no results, try a more general search
            if not search_results:
                fallback_query = f"qobuz.com {query}"
                logger.info(f"{YELLOW}Trying fallback search: {fallback_query}{RESET}")
                
                try:
                    for url in google_search(fallback_query, num_results=5, lang="en"):
                        if "qobuz.com" in url and url not in seen_urls:
                            seen_urls.add(url)
                            search_results.append({
                                "title": f"{query.title()} - {url.split('/')[-1].replace('-', ' ').title() or 'Qobuz Page'}",
                                "url": url
                            })
                except Exception as e:
                    logger.info(f"{RED}Fallback search failed: {str(e)}{RESET}")
            
            # Don't output the number of matches to simplify output
            return search_results
            
        except Exception as e:
            logger.info(f"{RED}Error during Google search: {str(e)}{RESET}")
            return []
