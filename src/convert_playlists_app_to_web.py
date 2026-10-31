import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
import json
import os
import re

class PlaylistToOldIdConverter(Gtk.Window):
    def __init__(self):
        super().__init__(title="YouTube Playlist URL to ID Converter")
        self.set_default_size(300, 100)
        self.set_position(Gtk.WindowPosition.CENTER)
        
        # Instantly open file selector windows upon launch
        self.run_file_selection()

    def extract_video_id(self, url_string):
        """Extracts exactly the 11-character YouTube video ID from any full URL path string."""
        if not url_string:
            return None
        
        clean_string = str(url_string).strip()
        
        # Regex captures exactly the trailing 11 valid characters of the YouTube ID sequence
        match = re.search(r'([a-zA-Z0-9_-]{11})$', clean_string)
        if match:
            return match.group(1)
        return None

    def run_file_selection(self):
        # 1. Dialog for choosing the input file containing full length URLs (OPEN mode)
        input_dialog = Gtk.FileChooserDialog(
            title="Select NEW Playlists JSON File to Reverse",
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        input_dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.ACCEPT
        )

        filter_json = Gtk.FileFilter()
        filter_json.set_name("JSON Files (*.json)")
        filter_json.add_pattern("*.json")
        input_dialog.add_filter(filter_json)

        input_dialog.show_all()
        response = input_dialog.run()

        if response != Gtk.ResponseType.ACCEPT:
            input_dialog.destroy()
            Gtk.main_quit()
            return

        source_path = input_dialog.get_filename()
        input_dialog.destroy()

        # 2. Dialog for choosing the output file to write the compact keys (SAVE mode)
        output_dialog = Gtk.FileChooserDialog(
            title="Save OLD Style Playlists JSON As",
            parent=self,
            action=Gtk.FileChooserAction.SAVE
        )
        output_dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.ACCEPT
        )
        output_dialog.set_current_name("playlists_old_id_format.json")
        output_dialog.set_do_overwrite_confirmation(True)
        output_dialog.add_filter(filter_json)

        output_dialog.show_all()
        response = output_dialog.run()

        if response != Gtk.ResponseType.ACCEPT:
            output_dialog.destroy()
            Gtk.main_quit()
            return

        target_path = output_dialog.get_filename()
        output_dialog.destroy()

        if not target_path.lower().endswith('.json'):
            target_path += '.json'

        # Execute structural decomposition loop down to ID keys
        self.convert_to_old_id_format(source_path, target_path)

    def convert_to_old_id_format(self, source_path, target_path):
        try:
            with open(source_path, 'r', encoding='utf-8') as f:
                new_data = json.load(f)

            old_format_data = {}
            processed_tracks_count = 0

            for playlist_name, tracks in new_data.items():
                old_format_data[playlist_name] = []
                
                for track in tracks:
                    video_id = None
                    title = "Unknown Track"

                    # Format match 1: Target incoming standard dictionary "url" schema properties
                    if isinstance(track, dict) and "url" in track:
                        video_id = self.extract_video_id(track["url"])
                        title = track.get("title", "Unknown Track")
                    
                    # Format match 2: If the profile already has an active "id" key property mapping
                    elif isinstance(track, dict) and "id" in track:
                        video_id = self.extract_video_id(track["id"])
                        title = track.get("title", "Unknown Track")

                    # Format match 3: Base textual plain string fallback mapping conversions
                    elif isinstance(track, str):
                        title = track

                    # Assemble historical mapping dictionary key entries safely
                    if video_id:
                        new_track = {
                            "id": video_id,
                            "title": title
                        }
                        old_format_data[playlist_name].append(new_track)
                        processed_tracks_count += 1
                    else:
                        # Append string fallback assignments to preserve structural data block loops
                        old_format_data[playlist_name].append({
                            "id": "",
                            "title": title
                        })

            # Save modified downstream tracking array properties back onto the local file disk blocks
            with open(target_path, 'w', encoding='utf-8') as f:
                json.dump(old_format_data, f, ensure_ascii=False, indent=4)

            self.show_message_dialog(
                Gtk.MessageType.INFO,
                "Reverse Conversion Successful",
                f"Successfully reversed {processed_tracks_count} tracks!\n\nAll entries are now strictly formatted with the old layout:\n\"id\": \"...\"\n\nSaved to:\n{target_path}"
            )

        except Exception as e:
            self.show_message_dialog(
                Gtk.MessageType.ERROR,
                "Reverse Conversion Failed",
                f"An error occurred while reversing the data structure layout arrays:\n{e}"
            )

    def show_message_dialog(self, message_type, title, text):
        """Displays a clean native GTK popup response dialog message block."""
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=message_type,
            buttons=Gtk.ButtonsType.OK,
            text=title
        )
        dialog.format_secondary_text(text)
        dialog.run()
        dialog.destroy()
        Gtk.main_quit()

if __name__ == "__main__":
    app = PlaylistToOldIdConverter()
    Gtk.main()
