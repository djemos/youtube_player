#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, GdkPixbuf
import subprocess
import threading
import sys
import json
import time
import os
import socket

# Internationalization Setup
import locale
import gettext

APP_NAME = "youtube_player" 

# GLOBAL CONFIGURATION: Change this number to easily control the max search items
MAX_RESULTS = 150

locale.setlocale(locale.LC_ALL, '')
locale.bindtextdomain(APP_NAME, "/usr/share/locale")

if hasattr(locale, 'bind_textdomain_codeset'):
    locale.bind_textdomain_codeset(APP_NAME, "UTF-8")

gettext.bindtextdomain(APP_NAME, "/usr/share/locale")
gettext.textdomain(APP_NAME)
_ = gettext.gettext


class YouTubeInsidePlayer(Gtk.Window):
    def __init__(self):
        # Auto-cleanup of old orphaned _MEI folders from previous crashes
        try:
            current_mei = sys._MEIPASS if hasattr(sys, '_MEIPASS') else ""
            tmp_dir = "/tmp"
            if os.path.exists(tmp_dir):
                for folder in os.listdir(tmp_dir):
                    if folder.startswith("_MEI") and folder != os.path.basename(current_mei):
                        full_path = os.path.join(tmp_dir, folder)
                        if os.path.isdir(full_path) and os.getuid() == os.stat(full_path).st_uid:
                            subprocess.Popen(["rm", "-rf", full_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception: pass
        super().__init__(title=_("YouTube Music & Video Player"))
        
        # Sizing UI properties
        self.set_default_size(1150, 600)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_border_width(12)
        
        # Handle icon asset paths inside user workspace safely
        self.icon_path = os.path.expanduser("/usr/share/pixmaps/youtube_player.png")
        os.makedirs(os.path.dirname(self.icon_path), exist_ok=True)
        if not os.path.exists(self.icon_path):
            icon_url = "https://flaticon.com"
            subprocess.Popen(["wget", "-q", "-O", self.icon_path, icon_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        self.app_pixbuf = None
        try:
            if os.path.exists(self.icon_path):
                self.app_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(self.icon_path, 128, 128, True)
                self.set_icon(self.app_pixbuf)
        except Exception: pass
        
        self.video_urls = []
        self.mpv_process = None
        self.search_process = None  
        self.current_volume = 100 
        self.is_fullscreen = False 
        self.is_paused = False 
        self.current_url = ""
        self.current_title = ""
        self.mpv_socket = "/tmp/mpv-active-player-socket"
        self.search_lock = threading.Lock()
        self.ipc_thread_active = False # Tracks lifecycle monitor loop
        self.autoplay_enabled = False  # Disabled by default
        
        vbox_main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(vbox_main)

        # 1. Top section: Search Bar Components
        self.search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        vbox_main.pack_start(self.search_box, False, False, 0)
        
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text(_("Type song title or artist..."))
        self.entry.connect("activate", self.on_search_submitted)
        self.search_box.pack_start(self.entry, True, True, 0)
        
        # Clear Button implementation
        self.clear_button = Gtk.Button()
        btn_clear_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_clear = Gtk.Image.new_from_icon_name("edit-clear", Gtk.IconSize.BUTTON)
        lbl_clear = Gtk.Label(label=_("Clear"))
        btn_clear_box.pack_start(img_clear, False, False, 0)
        btn_clear_box.pack_start(lbl_clear, False, False, 0)
        self.clear_button.add(btn_clear_box)
        self.clear_button.connect("clicked", self.on_clear_clicked)
        self.search_box.pack_start(self.clear_button, False, False, 0)

        # Visual loading spinner for deep queries (150 results)
        self.spinner = Gtk.Spinner()
        self.search_box.pack_start(self.spinner, False, False, 4)
        # Search button initialization
        self.search_button = Gtk.Button()
        btn_search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_search = Gtk.Image.new_from_icon_name("edit-find", Gtk.IconSize.BUTTON)
        lbl_search = Gtk.Label(label=_("Search"))
        btn_search_box.pack_start(img_search, False, False, 0)
        btn_search_box.pack_start(lbl_search, False, False, 0)
        self.search_button.add(btn_search_box)
        self.search_button.connect("clicked", self.on_search_submitted)
        self.search_box.pack_start(self.search_button, False, False, 0)

        # 2. Middle section: Content Display Splitter
        hbox_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        vbox_main.pack_start(hbox_content, True, True, 0)

        # Left Column: Search Results TreeView
        self.scroll_window = Gtk.ScrolledWindow()
        self.scroll_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scroll_window.set_size_request(450, -1)
        hbox_content.pack_start(self.scroll_window, False, False, 0)
        
        self.list_store = Gtk.ListStore(str, str, str)
        self.tree_view = Gtk.TreeView(model=self.list_store)
        self.tree_view.set_enable_search(False)
        self.tree_view.connect("row-activated", self.on_row_double_clicked)
        self.scroll_window.add(self.tree_view)
        
        col_num = Gtk.TreeViewColumn("#", Gtk.CellRendererText(), text=0)
        col_num.set_min_width(35)
        self.tree_view.append_column(col_num)
        
        col_title = Gtk.TreeViewColumn(_("Title"), Gtk.CellRendererText(), text=1)
        col_title.set_expand(True)
        self.tree_view.append_column(col_title)
        
        col_duration = Gtk.TreeViewColumn(_("Source"), Gtk.CellRendererText(), text=2)
        col_duration.set_min_width(65)
        self.tree_view.append_column(col_duration)

        # Right Column: Embedded Video Pipeline Surface
        right_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hbox_content.pack_start(right_vbox, True, True, 0)

        self.video_event_box = Gtk.EventBox()
        self.video_event_box.connect("button-press-event", self.on_video_clicked)
        right_vbox.pack_start(self.video_event_box, True, True, 0)

        self.video_overlay = Gtk.Overlay()
        self.video_event_box.add(self.video_overlay)
        
        self.video_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.video_container.set_size_request(540, 380)
        self.video_container.set_border_width(14)
        self.video_overlay.add(self.video_container)
        
        self.fs_info_label = Gtk.Label()
        fs_label_text = _("DOUBLE CLICK HERE OR PRESS ESC OR F TO EXIT FULL SCREEN")
        self.fs_info_label.set_markup(f"<span background='#222222' foreground='#3584e4' size='medium'><b> {fs_label_text} </b></span>")
        self.fs_info_label.set_valign(Gtk.Align.START)
        self.fs_info_label.set_halign(Gtk.Align.CENTER)
        self.fs_info_label.set_margin_top(2)
        self.fs_info_label.set_no_show_all(True)
        self.video_overlay.add_overlay(self.fs_info_label)
        
        self.socket = None

        # Inline Media Playback Controls
        self.video_control_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        right_vbox.pack_start(self.video_control_bar, False, False, 0)

        self.pause_button = Gtk.Button(label=f"⏸ {_( 'Pause')}")
        self.pause_button.connect("clicked", self.toggle_pause)
        self.pause_button.set_sensitive(False)
        self.video_control_bar.pack_start(self.pause_button, False, False, 0)

        volume_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_vol = Gtk.Image.new_from_icon_name("audio-volume-high", Gtk.IconSize.BUTTON)
        lbl_vol = Gtk.Label(label=_("Volume:"))
        volume_box.pack_start(img_vol, False, False, 0)
        volume_box.pack_start(lbl_vol, False, False, 0)
        self.video_control_bar.pack_start(volume_box, False, False, 0)

        self.volume_slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume_slider.set_value(self.current_volume)
        self.volume_slider.set_size_request(150, -1)
        self.volume_slider.connect("value-changed", self.on_volume_changed)
        self.video_control_bar.pack_start(self.volume_slider, False, False, 0)
        
        self.fullscreen_button = Gtk.Button()
        btn_fs_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_fs = Gtk.Image.new_from_icon_name("view-fullscreen", Gtk.IconSize.BUTTON)
        lbl_fs = Gtk.Label(label=_("Full Screen (F)"))
        btn_fs_box.pack_start(img_fs, False, False, 0)
        btn_fs_box.pack_start(lbl_fs, False, False, 0)
        self.fullscreen_button.add(btn_fs_box)
        self.fullscreen_button.connect("clicked", lambda b: self.toggle_fullscreen())
        self.fullscreen_button.set_sensitive(False)
        self.fullscreen_button.set_size_request(150, -1)
        self.video_control_bar.pack_start(self.fullscreen_button, False, False, 0)

        # Autoplay Toggle Button Initialization
        self.autoplay_check = Gtk.CheckButton(label=_("Autoplay Next"))
        self.autoplay_check.set_active(self.autoplay_enabled)
        self.autoplay_check.connect("toggled", self.on_autoplay_toggled)
        self.video_control_bar.pack_start(self.autoplay_check, False, False, 0)
        # 3. Bottom section: Controls & Status Frame Bar
        self.bottom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox_main.pack_start(self.bottom_box, False, False, 0)
        
        self.buttons_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.bottom_box.pack_start(self.buttons_row, False, False, 0)
        
        self.play_button = Gtk.Button(label=f"▶ {_( 'Play')}")
        self.play_button.connect("clicked", self.on_play_button_clicked)
        self.play_button.set_sensitive(False)
        self.buttons_row.pack_start(self.play_button, False, False, 0)
        
        self.stop_button = Gtk.Button(label=f"⏹ {_( 'Stop')}")
        self.stop_button.connect("clicked", lambda b: self.stop_playback())
        self.stop_button.set_sensitive(False)
        self.buttons_row.pack_start(self.stop_button, False, False, 0)

        self.download_button = Gtk.Button(label=f"♫ {_( 'Download MP3')}")
        self.download_button.connect("clicked", lambda b: self.start_download("mp3"))
        self.download_button.set_sensitive(False)
        self.buttons_row.pack_start(self.download_button, False, False, 0)

        self.download_video_button = Gtk.Button(label=f"♫ {_( 'Download MP4')}")
        self.download_video_button.connect("clicked", lambda b: self.start_download("mp4"))
        self.download_video_button.set_sensitive(False)
        self.buttons_row.pack_start(self.download_video_button, False, False, 0)
        
        self.about_button = Gtk.Button()
        btn_about_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_about = Gtk.Image.new_from_icon_name("help-about", Gtk.IconSize.BUTTON)
        lbl_about = Gtk.Label(label=_("About"))
        btn_about_box.pack_start(img_about, False, False, 0)
        btn_about_box.pack_start(lbl_about, False, False, 0)
        self.about_button.add(btn_about_box)
        self.about_button.connect("clicked", self.show_about_dialog)
        self.buttons_row.pack_start(self.about_button, False, False, 0)
        
        self.status_frame = Gtk.Frame()
        self.status_frame.set_shadow_type(Gtk.ShadowType.IN)
        self.bottom_box.pack_start(self.status_frame, False, False, 0)

        self.status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.status_box.set_margin_top(6)
        self.status_box.set_margin_bottom(6)
        self.status_box.set_margin_start(8)
        self.status_box.set_margin_end(8)
        
        self.status_image = Gtk.Image.new_from_icon_name("dialog-information", Gtk.IconSize.MENU)
        self.status_label = Gtk.Label()
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_line_wrap(True)
        self.status_label.set_markup(f"<span foreground='gray'><i>{_('Ready.')}</i></span>")
        
        self.status_box.pack_start(self.status_image, False, False, 0)
        self.status_box.pack_start(self.status_label, True, True, 0)
        self.status_frame.add(self.status_box)

        self.connect("key-release-event", self.on_key_release)
        self.connect("destroy", self.on_destroy)

    def update_status(self, text, color="#3584e4", icon_name="dialog-information"):
        GLib.idle_add(self.status_image.set_from_icon_name, icon_name, Gtk.IconSize.MENU)
        GLib.idle_add(self.status_label.set_markup, f"<span foreground='{color}'><i>{text}</i></span>")

    def on_clear_clicked(self, button):
        self.entry.set_text("")
        self.entry.grab_focus()
        # Automatically clean yt-dlp internal tracking cache to speed up deep listings
        self.update_status(_("Cleaning yt-dlp cache storage..."), icon_name="user-trash")
        threading.Thread(target=self.clear_ytdlp_cache_thread, daemon=True).start()

    def clear_ytdlp_cache_thread(self):
        """ Safe backend execution block to empty yt-dlp caching pipes. """
        try:
            subprocess.run(["yt-dlp", "--rm-cache-dir"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.update_status(_("Cache cleared successfully. System ready."), "green", icon_name="emblem-ok")
        except Exception:
            self.update_status(_("Failed to purge cache data."), "red", icon_name="dialog-warning")

    def on_autoplay_toggled(self, button):
        """ Updates the state when the autoplay checkbox is toggled. """
        self.autoplay_enabled = button.get_active()

    def send_mpv_ipc_command(self, cmd_list):
        """ Native Python UNIX Socket client implementation. """
        if self.mpv_process and self.mpv_process.poll() is None:
            if os.path.exists(self.mpv_socket):
                try:
                    payload = json.dumps({"command": cmd_list}) + "\n"
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    client.connect(self.mpv_socket)
                    client.sendall(payload.encode('utf-8'))
                    client.close()
                except Exception: pass

    def send_mpv_ipc_query(self, cmd_list):
        """ Native implementation to request states from the mpv engine instance with robust event filtering. """
        if self.mpv_process and self.mpv_process.poll() is None:
            if os.path.exists(self.mpv_socket):
                try:
                    payload = json.dumps({"command": cmd_list}) + "\n"
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    client.settimeout(0.3)
                    client.connect(self.mpv_socket)
                    client.sendall(payload.encode('utf-8'))
                    
                    response_data = client.recv(4096).decode('utf-8')
                    client.close()
                    
                    lines = response_data.strip().split('\n')
                    for line in lines:
                        if not line.strip(): 
                            continue
                        try:
                            parsed = json.loads(line.strip())
                            if "error" in parsed:
                                return parsed
                        except Exception: pass
                except Exception: pass
        return None

    def on_search_submitted(self, widget):
        user_input = self.entry.get_text().strip()
        if not user_input: return
        self.search_button.set_sensitive(False)
        self.entry.set_sensitive(False)
        
        # Trigger and animate the visual loading spinner on the layout pipe
        self.spinner.start()
        
        self.list_store.clear()
        self.video_urls = []
        self.stop_playback()
        self.update_status(_("Searching YouTube (fetching 150 items)..."), icon_name="edit-find")
        threading.Thread(target=self.fetch_youtube_results, args=(user_input,), daemon=True).start()
        
    def fetch_youtube_results(self, phrase):
        with self.search_lock:
            try:
                # FIXED: Dynamically inject MAX_RESULTS into the yt-dlp command string
                cmd = ["yt-dlp", "--no-cache-dir", "--skip-download", "--flat-playlist", "--print", "%(url)s\t%(title)s", f"ytsearch{MAX_RESULTS}:{phrase}"]
                self.search_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                local_results = []
                seen_urls = set()
                while True:
                    if not self.search_process: break
                    line = self.search_process.stdout.readline()
                    if not line: break
                    if "\t" not in line: continue
                    url, title = line.split("\t", 1)
                    url = url.strip()
                    title = title.strip()
                    if not url.startswith("http"): url = f"https://youtube.com{url}"
                    if url in seen_urls: continue
                    seen_urls.add(url)
                    local_results.append((url, title))
                    # FIXED: Dynamically match the loop breaker to MAX_RESULTS
                    if len(local_results) >= MAX_RESULTS: break
                if self.search_process:
                    self.search_process.kill()
                    self.search_process.wait()
                    self.search_process = None
                GLib.idle_add(self.update_gui_list, local_results)
            except Exception:
                if self.search_process:
                    try: 
                        self.search_process.kill()
                        self.search_process.wait()
                    except Exception: pass
                    self.search_process = None
                self.update_status(_("Error during search."), "red", icon_name="dialog-warning")
                GLib.idle_add(self.spinner.stop)
                GLib.idle_add(self.search_button.set_sensitive, True)
                GLib.idle_add(self.entry.set_sensitive, True)


    def update_gui_list(self, results):
        self.list_store.clear()
        self.video_urls = []
        self.stop_playback()
        count = 1
        for url, title in results:
            self.video_urls.append(url)
            self.list_store.append([str(count), title, "YouTube"])
            count += 1
            
        # Stops spinner animation when data has fully landed on the screen
        self.spinner.stop()
        
        self.update_status(f"{_('Displaying')} {len(self.video_urls)} {_('unique results.')}", icon_name="emblem-ok")
        self.play_button.set_sensitive(True)
        self.download_button.set_sensitive(True)
        self.download_video_button.set_sensitive(True)
        self.search_button.set_sensitive(True)
        self.entry.set_sensitive(True)
        
        if not self.get_icon() and os.path.exists(self.icon_path):
            try:
                self.app_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(self.icon_path, 128, 128, True)
                self.set_icon(self.app_pixbuf)
            except Exception: pass
        return False

    def get_selected_url(self):
        selection = self.tree_view.get_selection()
        model, treeiter = selection.get_selected()
        if treeiter is not None:
            row_num_str = model.get_value(treeiter, 0)
            title = model.get_value(treeiter, 1)
            index = int(row_num_str) - 1
            if 0 <= index < len(self.video_urls): return self.video_urls[index], title
        return None, None

    def play_video(self, url, title):
        if not url: return
        self.current_url = url
        self.current_title = title
        GLib.idle_add(self.recreate_socket_and_start_mpv, url, title)

    def recreate_socket_and_start_mpv(self, url, title):
        for child in self.video_container.get_children():
            self.video_container.remove(child)
            
        self.socket = Gtk.Socket()
        self.socket.set_size_request(540, 380)
        self.video_container.pack_start(self.socket, True, True, 0)
        self.video_container.show_all()
        self.socket.realize()
        
        socket_id = self.socket.get_id()
        self.update_status(f"{_('Loading video:')} {title}", icon_name="media-seek-forward")
        self.stop_button.set_sensitive(True)
        self.fullscreen_button.set_sensitive(True)
        self.pause_button.set_sensitive(True)
        self.pause_button.set_label(f"⏸ {_( 'Pause')}")
        self.is_paused = False

        # Optimized MPV engine configuration options with session persistence hooks
        mpv_cmd = [
            "mpv",
            f"--wid={socket_id}",
            "--vo=gpu",
            "--gpu-context=x11egl",
            "--input-vo-keyboard=no",
            "--keep-open=yes",           
            "--idle=yes",                
            "--osc=yes",
            "--script-opts=osc-visibility=always",
            f"--input-ipc-server={self.mpv_socket}",
            f"--volume={int(self.current_volume)}",
            "--force-window=yes",
            url
        ]
        
        try:
            self.mpv_process = subprocess.Popen(
                mpv_cmd, 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL
            )
            self.update_status(f"{_('Playing:')} {title}", icon_name="media-playback-start")
            
            # Spawn the background tracking thread for window session persistence
            self.ipc_thread_active = True
            threading.Thread(target=self.monitor_mpv_playback, daemon=True).start()
            
        except Exception:
            self.update_status(_("Failed to start playback."), "red", icon_name="dialog-error")
        return False
    def monitor_mpv_playback(self):
        """ Checks IPC socket state to cleanly exit fullscreen or play next video when finished. """
        while self.ipc_thread_active:
            if not self.mpv_process or self.mpv_process.poll() is not None:
                break
            
            # Query the core engine for EOF status property records
            res = self.send_mpv_ipc_query(["get_property", "eof-reached"])
            
            # Check if the cleaned response confirms EOF success
            if res and isinstance(res, dict):
                if res.get("error") == "success" and res.get("data") is True:
                    # Target reached: Safely drop out of fullscreen if active
                    if self.is_fullscreen:
                        GLib.idle_add(self.toggle_fullscreen)
                    
                    # Trigger next video injection
                    if self.autoplay_enabled:
                        GLib.idle_add(self.play_next_video)
                    break
                    
            time.sleep(0.4)

    def play_next_video(self):
        """ Automatically selects and plays the next row in the TreeView list. """
        selection = self.tree_view.get_selection()
        model, treeiter = selection.get_selected()
        
        if treeiter is not None:
            # Get the next row iterator
            next_iter = model.iter_next(treeiter)
            if next_iter is not None:
                # Select the next row visually in the UI
                selection.select_iter(next_iter)
                # Trigger the playback sequence on a fresh safe thread
                url, title = self.get_selected_url()
                if url:
                    self.stop_playback()
                    threading.Thread(target=self.play_video, args=(url, title), daemon=True).start()

    def on_volume_changed(self, scroll):
        self.current_volume = self.volume_slider.get_value()
        self.send_mpv_ipc_command(["set_property", "volume", int(self.current_volume)])

    def toggle_pause(self, button):
        if self.mpv_process and self.mpv_process.poll() is None:
            self.is_paused = not self.is_paused
            self.send_mpv_ipc_command(["set_property", "pause", self.is_paused])
            if self.is_paused:
                self.pause_button.set_label(f"▶ {_( 'Resume')}")
                self.update_status(_("Playback paused."), icon_name="media-playback-pause")
            else:
                self.pause_button.set_label(f"⏸ {_( 'Pause')}")
                self.update_status(f"{_('Playing:')} {self.current_title}", icon_name="media-playback-start")

    def toggle_fullscreen(self):
        if not self.is_fullscreen:
            # Hide layout structures before applying fullscreen metrics
            self.search_box.hide()
            self.scroll_window.hide()
            self.video_control_bar.hide()
            self.buttons_row.hide()
            
            self.fullscreen() 
            self.is_fullscreen = True
            
            self.fs_info_label.show() 
            self.update_status(_("Fullscreen Active. Press F, ESC or Double Click to restore."), icon_name="view-fullscreen")
        else:
            self.fs_info_label.hide() 
            
            self.search_box.show()
            self.scroll_window.show()
            self.video_control_bar.show()
            self.buttons_row.show()
            
            self.unfullscreen() 
            self.is_fullscreen = False
            self.update_status(f"{_('Playing:')} {self.current_title}", icon_name="media-playback-start")

    def on_video_clicked(self, widget, event):
        if event.type == Gdk.EventType._2BUTTON_PRESS: 
            self.toggle_fullscreen()
            return True
        return False

    def on_key_release(self, widget, event):
        keyname = Gdk.keyval_name(event.keyval)
        
        if self.get_focus() == self.entry:
            if keyname == "Escape":
                self.tree_view.grab_focus()
                return True
            return False

        # FIXED: Added support for both English (f/F) and Greek (Greek_phi/Greek_PHI) layouts
        if keyname in ["f", "F", "Greek_phi", "Greek_PHI"]:
            self.toggle_fullscreen()
            return True
        elif keyname == "Escape" and self.is_fullscreen:
            self.toggle_fullscreen()
            return True
            
        return False


    def show_about_dialog(self, button):
        about = Gtk.AboutDialog()
        about.set_transient_for(self) 
        about.set_program_name(_("YouTube Music & Video Player"))
        about.set_version("1.0")
        about.set_copyright("Copyright © 2026")
        about.set_comments(_("An advanced, thread-safe embedded player for YouTube videos and music with standalone download support."))
        about.set_website("https://github.com")
        about.set_authors(["Dimitris Tzemos <dijemos@gmail.com>"])
        about.set_translator_credits(_("translator-credits"))
        
        if self.app_pixbuf:
            about.set_logo(self.app_pixbuf)
        else:
            about.set_logo_icon_name("multimedia-video-player")
            
        about.set_license_type(Gtk.License.GPL_3_0)
        about.connect("response", lambda d, r: d.destroy())
        about.show()

    def start_download(self, file_type):
        url, title = self.get_selected_url()
        if url:
            self.download_button.set_sensitive(False)
            self.download_video_button.set_sensitive(False)
            self.update_status(f"{_('Started downloading')} {file_type.upper()}: {title[:25]}...", icon_name="folder-download")
            threading.Thread(target=self.download_thread, args=(url, title, file_type), daemon=True).start()

    def download_thread(self, url, title, file_type):
        try:
            download_dir = os.path.expanduser("~/Downloads")
            if not os.path.exists(download_dir): download_dir = os.path.expanduser("~")
            
            if file_type == "mp3":
                cmd = ["yt-dlp", "--no-cache-dir", "-x", "--audio-format", "mp3", "--audio-quality", "0", "-o", f"{download_dir}/%(title)s.%(ext)s", url]
            else:
                cmd = ["yt-dlp", "--no-cache-dir", "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]", "-o", f"{download_dir}/%(title)s.%(ext)s", url]
                
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode == 0:
                self.update_status(f"{_('Download completed inside')} ~/Downloads!", "green", icon_name="emblem-ok")
            else:
                self.update_status(_("Download failed. Make sure ffmpeg is installed."), "red", icon_name="dialog-error")
        except Exception:
            self.update_status(_("Error during download process."), "red", icon_name="dialog-error")
            
        GLib.idle_add(self.download_button.set_sensitive, True)
        GLib.idle_add(self.download_video_button.set_sensitive, True)

    def stop_playback(self):
        self.ipc_thread_active = False 
        if self.is_fullscreen: 
            self.toggle_fullscreen()
        
        if self.mpv_process:
            try:
                self.mpv_process.terminate()
                self.mpv_process.wait(timeout=1)
            except Exception: 
                try: self.mpv_process.kill()
                except Exception: pass
            self.mpv_process = None
            
        if self.search_process:
            try:
                self.search_process.kill()
                self.search_process.wait()
            except Exception: pass
            self.search_process = None
            
        if os.path.exists(self.mpv_socket):
            try: os.remove(self.mpv_socket)
            except Exception: pass
            
        GLib.idle_add(self.stop_button.set_sensitive, False)
        GLib.idle_add(self.fullscreen_button.set_sensitive, False)
        GLib.idle_add(self.pause_button.set_sensitive, False)
        self.pause_button.set_label(f"⏸ {_( 'Pause')}")
        self.is_paused = False
        self.update_status(_("Ready."), icon_name="dialog-information")

    def on_play_button_clicked(self, button):
        url, title = self.get_selected_url()
        if url: 
            self.stop_playback() 
            threading.Thread(target=self.play_video, args=(url, title), daemon=True).start()

    def on_row_double_clicked(self, tree_view, path, column):
        url, title = self.get_selected_url()
        if url: 
            self.stop_playback() 
            threading.Thread(target=self.play_video, args=(url, title), daemon=True).start()

    def on_destroy(self, widget):
        self.stop_playback()
        time.sleep(0.1) 
        Gtk.main_quit()

if __name__ == "__main__":
    win = YouTubeInsidePlayer()
    win.show_all()
    Gtk.main()
