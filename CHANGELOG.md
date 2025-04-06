# Changelog

All notable changes to the QDL project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.12.0] - 2025-04-06

### Changed
- Streamlined label search output with a minimalistic presentation
- Removed redundant debugging messages for a cleaner user experience
- Reordered search menu options to a more logical flow: Artists, Albums, Tracks, Playlists, Label search
- Enhanced console output with improved color formatting:
  - Status information appears in white (search status, found URLs, processing messages)
  - Success and download information appears in green (download confirmation, queue counts)

### Added
- New folder and track naming system based on search mode
- Top-level folder creation control with `create_top_folder` setting in format_config.ini
- Explicit `top_folder_format` parameter for controlling root folder naming
- Search mode aliases in DEFAULT section for mapping UI modes to format configurations
- Support for playlist folders with proper naming using the {playlist} tag
- Support for label collections with proper naming using the {label} tag
- Multiple naming modes including artist_discography_dg, album, artist, label, and playlist
- Customizable folder and track name formats through config files

## [0.11.1] - 2025-04-06

### Changed
- Streamlined label search output with a minimalistic presentation
- Removed redundant debugging messages for a cleaner user experience
- Maintained interactive label selection menu for better user control
- Enhanced console output with improved color formatting:
  - Status information appears in white (search status, found URLs, processing messages)
  - Success and download information appears in green (download confirmation, queue counts)

## [0.11] - 2025-04-05

### Major Changes
- Full fork and rebranding from qobuz-dl to QDL
- Updated all references to use the new GitHub repository at https://github.com/dragonherb/qdl
- Command has been changed from "qobuz-dl" to "qdl" in all messages and documentation

### Added
- Default start mode feature via `default_start_mode` in config.ini
- Automatic command execution when no arguments are given (configured via config.ini)

### Changed
- Improved code organization and removed redundant backup folders
- Enhanced user experience with more intuitive command handling

## [0.10] - 2025-04-01

### Added
- Modern Python packaging with pyproject.toml
- Future-proof installation support for pip 25.1+
- Added default start mode feature via `default_start_mode` in config.ini

### Fixed
- Fixed deprecation warnings during editable installs
- Updated build system configuration to use setuptools>=64.0.0

### Notes
- "Modernized Python packaging with pyproject.toml"

## [0.09] - 2025-03-30

### Fixed
- Completed and fully functional label search feature
- Label URL processing now correctly handles various formats and regional variants
- Fixed region code standardization to use "se-en" for reliable label downloads
- Improved handling of "download-streaming-albums" in URLs
- Enhanced debugging output to show URL transformations
- Fixed CTRL+C handling during label downloads to exit cleanly

### Added
- Support for more regional URL variants (us-en, ar-es, gb-en, fr-fr, de-de)
- Duplicate URL detection in search results

### Notes
- "Milestone: Fully functional Google label search with robust URL handling"

## [0.08] - 2025-03-30

### Fixed
- Label search URL processing to handle various formats and clean them properly
- Region code standardization - now using "se-en" for all label URLs
- Fixed download method for label search results
- Improved handling of duplicate search results

### Notes
- "Fixed label search functionality to ensure successful downloads from Qobuz labels"

## [0.07] - 2025-03-30

### Added
- "Label search (Google)" feature in the interactive menu
- Google web scraping functionality to find Qobuz label pages using googlesearch-python library
- Ability to select and download content from record labels

### Notes
- "Added label search via Google web scraping"

## [0.06] - 2025-03-29

### Fixed
- Bug in interactive mode where quality selection menu always defaulted to "Lossless" instead of respecting user's configured quality
- Fixed display issue in quality selection menu that was showing "None" instead of proper quality descriptions

### Notes
- "Interactive quality selection improvements"

## [0.05] - 2025-03-29

### Changed
- Removed the obsolete qobuz_dl directory
- Cleaned up project structure

### Notes
- "Final cleanup of old package directory"

## [0.04] - 2025-03-29

### Changed
- Renamed the package directory from "qobuz_dl" to "qobuz_downloader"
- Updated all internal import statements to reference the new package name
- Modified function name "qobuz_dl_args" to "qdl_args" for consistency
- Updated setup.py entry points to use the new package name
- Changed reset message in qopy.py to reference QDL instead of qobuz-dl

### Notes
- "Package refactoring for improved naming clarity"

## [0.03] - 2025-03-29

### Changed
- Renamed all program references from "qobuz-dl" to "QDL"
- Updated configuration directory from "qobuz-dl" to "QDL"
- Changed database filename from "qobuz_dl.db" to "QDL.db"
- Updated default download directory from "Qobuz Downloads" to "QDL Downloads"
- Modified package name in setup.py
- Updated README.md with new program name references

### Notes
- "Full rebranding from qobuz-dl to QDL"

## [0.02] - 2025-03-29

### Changes
- No functional changes here, just housekeeping

### Notes
- "freshstart with new name QDL"

## [0.01] - 2025-03-29

### Added
- Initial project setup
- Created CHANGELOG.md for tracking version changes

### Notes
- "initial version"
