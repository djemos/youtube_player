import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
import json
import os
import re

class PlaylistExactUrlConverter(Gtk.Window):
    def __init__(self):
        super().__init__(title="YouTube Playlist URL Fixer")
        self.set_default_size(300, 100)
        self.set_position(Gtk.WindowPosition.CENTER)
        
        # Instantly open file selector windows upon launch
        self.run_file_selection()

    def extract_video_id(self, id_or_url_string):
        """Extracts exactly the 11-character YouTube video ID from any input pattern."""
        if not id_or_url_string:
            return None
        
        clean_string = str(id_or_url_string).strip()
        
        # Regex captures exactly the trailing 11 valid characters of the YouTube ID sequence
        match = re.search(r'([a-zA-Z0-9_-]{11})$', clean_string)
        if match:
            return match.group(1)
        return None

    def run_file_selection(self):
        # 1. Dialog for choosing the input file you want to change (OPEN mode)
        input_dialog = Gtk.FileChooserDialog(
            title="Select Playlists JSON File to Convert",
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

        # 2. Dialog for choosing the output file location to save the clean data (SAVE mode)
        output_dialog = Gtk.FileChooserDialog(
            title="Save Fixed Playlists JSON As",
            parent=self,
            action=Gtk.FileChooserAction.SAVE
        )
        output_dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.ACCEPT
        )
        output_dialog.set_current_name("playlists_fixed.json")
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

        # Execute structural translation mapping loops
        self.convert_to_standard_url(source_path, target_path)

    def convert_to_standard_url(self, source_path, target_path):
        try:
            with open(source_path, 'r', encoding='utf-8') as f:
                old_data = json.load(f)

            new_data = {}
            processed_tracks_count = 0

            for playlist_name, tracks in old_data.items():
                new_data[playlist_name] = []
                
                for track in tracks:
                    video_id = None
                    title = "Unknown Track"

                    # Format match 1: Check if "url" property key exists (even if broken or truncated)
                    if isinstance(track, dict) and "url" in track:
                        video_id = self.extract_video_id(track["url"])
                        title = track.get("title", "Unknown Track")

                    # Format match 2: Check if legacy "id" key property layout structure exists
                    elif isinstance(track, dict) and "id" in track:
                        video_id = self.extract_video_id(track["id"])
                        title = track.get("title", "Unknown Track")

                    # Format match 3: Fallback handling if object is plain textual track title string
                    elif isinstance(track, str):
                        title = track

                    # Assemble updated downstream tracking array properties safely if ID exists
                    if video_id:
                        standardized_url = f"https://www.youtube.com/watch?v={video_id}"
                        new_track = {
                            "url": standardized_url,
                            "title": title
                        }
                        new_data[playlist_name].append(new_track)
                        processed_tracks_count += 1
                    else:
                        # Append empty tracking string assignments to preserve playback logic structures
                        new_data[playlist_name].append({
                            "url": "",
                            "title": title
                        })

            # Save modified downstream tracking array properties back onto the local file disk blocks
            with open(target_path, 'w', encoding='utf-8') as f:
                json.dump(new_data, f, ensure_ascii=False, indent=4)

            self.show_message_dialog(
                Gtk.MessageType.INFO,
                "Conversion Successful",
                f"Successfully standardized {processed_tracks_count} tracks!\n\nAll links are now strictly formatted as:\nhttps://www.youtube.com/watch?v=...\n\nSaved to:\n{target_path}"
            )

        except Exception as e:
            self.show_message_dialog(
                Gtk.MessageType.ERROR,
                "Conversion Failed",
                f"An error occurred while reformatting the data:\n{e}"
            )

    def show_message_dialog(self, message_type, title, text):
        """Displays a clean native GTK popup message dialog."""
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
    app = PlaylistExactUrlConverter()
    Gtk.main()
