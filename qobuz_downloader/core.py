import logging
import os
import sys
import time
import random

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
from qobuz_downloader.color import CYAN, OFF, RED, YELLOW, DF, RESET, GREEN
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
        folder_format="{artist} - {album} ({year}) [{bit_depth}B-"
        "{sampling_rate}kHz]",
        track_format="{tracknumber}. {tracktitle}",
        smart_discography=False,
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

    def initialize_client(self, email, pwd, app_id, secrets):
        self.client = qopy.Client(email, pwd, app_id, secrets)
        logger.info(f"{YELLOW}Set max quality: {QUALITIES[int(self.quality)]}\n")

    def get_tokens(self):
        bundle = Bundle()
        self.app_id = bundle.get_app_id()
        self.secrets = [
            secret for secret in bundle.get_secrets().values() if secret
        ]  # avoid empty fields

    def download_from_id(self, item_id, album=True, alt_path=None):
        if handle_download_id(self.downloads_db, item_id, add_id=False):
            logger.info(
                f"{OFF}This release ID ({item_id}) was already downloaded "
                "according to the local database.\nUse the '--no-db' flag "
                "to bypass this."
            )
            return
        try:
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
                self.folder_format,
                self.track_format,
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
                f"{YELLOW}Downloading all the music from {content_name} "
                f"({url_type})!"
            )
            new_path = create_and_return_dir(
                os.path.join(self.directory, sanitize_filename(content_name))
            )

            if self.smart_discography and url_type == "artist":
                # change `save_space` and `skip_extras` for customization
                items = smart_discography_filter(
                    content,
                    save_space=True,
                    skip_extras=True,
                )
            else:
                items = [item[type_dict["iterable_key"]]["items"] for item in content][
                    0
                ]

            logger.info(f"{YELLOW}{len(items)} downloads in queue")
            for item in items:
                self.download_from_id(
                    item["id"],
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
            return option.get("text")

        try:
            item_types = ["Albums", "Tracks", "Artists", "Playlists", "Label search (Google)"]
            selected_type = pick(
                item_types, "I'll search for:\n[press Intro]")[0][
                :-1
            ].lower()
            
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
                title = (
                    f'*** RESULTS FOR "{query.title()}" ***\n\n'
                    "Select [space] the item(s) you want to download "
                    "(one or more)\nPress Ctrl + c to quit\n"
                    "Don't select anything to try another search"
                )
                selected_items = pick(
                    options,
                    title,
                    multiselect=True,
                    min_selection_count=0,
                    options_map_func=get_title_text,
                )
                if len(selected_items) > 0:
                    [final_url_list.append(i[0]["url"]) for i in selected_items]
                    y_n = pick(
                        ["Yes", "No"],
                        "Items were added to queue to be downloaded. "
                        "Keep searching?",
                    )
                    if y_n[0][0] == "N":
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
                logger.info(f"{YELLOW}Searching for label: {query} on Google...{RESET}")
                
                # Perform Google search for Qobuz label using googlesearch-python
                label_urls = self.search_label_on_google(query)
                
                if not label_urls:
                    logger.info(f"{OFF}No label results found{RESET}")
                    continue
                
                # Format options for display
                options = []
                for i, url_info in enumerate(label_urls):
                    # Print the original URL for debugging
                    logger.info(f"{YELLOW}Original URL: {url_info['url']}{RESET}")
                    
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
                            logger.info(f"{GREEN}Cleaned URL (removed hyphenated): {clean_url}{RESET}")
                    
                    # Also check for the space version
                    elif "Download Streaming Albums/" in original_url:
                        clean_url = original_url.replace("Download Streaming Albums/", "")
                        logger.info(f"{GREEN}Cleaned URL (removed spaced): {clean_url}{RESET}")
                    
                    # Always use se-en region code for label URLs since that has been confirmed to work
                    if "/label/" in clean_url:
                        # Find the region code in the URL
                        for region in ["/us-en/", "/ar-es/", "/gb-en/", "/fr-fr/", "/de-de/"]:
                            if region in clean_url:
                                # Replace with the working region code
                                clean_url = clean_url.replace(region, "/se-en/")
                                logger.info(f"{GREEN}Using se-en region: {clean_url}{RESET}")
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
                        # Using the globally imported pick function
                        selected = pick(
                            options, title, options_map_func=get_title_text
                        )
                        
                        # Use the cleaned URL for downloading
                        label_url = selected[0]["url"]
                        original_url = selected[0]["original_url"]
                        
                        # Debug log both URLs
                        logger.info(f"{YELLOW}Selected original URL: {original_url}{RESET}")
                        logger.info(f"{YELLOW}Selected cleaned URL for download: {label_url}{RESET}")
                        
                        final_url_list.append(label_url)
                        
                        # Instead of trying to download the label directly, use the handle_url method
                        # which already knows how to process label URLs properly
                        logger.info(f"{YELLOW}Processing label URL: {label_url}{RESET}")
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
                f"site:qobuz.com {query} record label"
            ]
            
            search_results = []
            seen_urls = set()  # Keep track of seen URLs to avoid duplicates
            
            # Try each search query
            for search_query in search_queries:
                logger.info(f"{YELLOW}Searching with query: {search_query}{RESET}")
                
                try:
                    # Add pause and random delays to avoid rate limiting
                    time.sleep(random.uniform(1.0, 2.0))
                    
                    # Use googlesearch-python to find Qobuz URLs
                    for url in google_search(search_query, num_results=5, lang="en"):
                        # Print original URL for debugging
                        logger.info(f"{YELLOW}Found URL: {url}{RESET}")
                        
                        # Skip if we've already seen this URL
                        if url in seen_urls:
                            logger.info(f"{YELLOW}Skipping duplicate URL: {url}{RESET}")
                            continue
                        
                        seen_urls.add(url)  # Add to seen URLs
                        
                        # More flexible pattern matching for Qobuz label URLs
                        if "qobuz.com" in url and ("/label/" in url or "label" in url):
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
                
                # If we found results, no need to try other queries
                if search_results:
                    break
            
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
            
            if search_results:
                logger.info(f"{GREEN}Found {len(search_results)} potential matches{RESET}")
            return search_results
            
        except Exception as e:
            logger.info(f"{RED}Error during Google search: {str(e)}{RESET}")
            return []
