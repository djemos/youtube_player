#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, GdkPixbuf, Gio
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

# GLOBAL CONFIGURATION
MAX_RESULTS = 50
CONFIG_DIR = os.path.expanduser("~/.config/youtube_player")
PLAYLISTS_FILE = os.path.join(CONFIG_DIR, "playlists.json")

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
        self.set_default_size(1300, 650) # Increased width for playlists layout
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
        self.ipc_thread_active = False 
        self.autoplay_enabled = False  
        self.last_clicked_view = "search" # Tracks which side was clicked last ("search" or "playlist")

        # Playlist state Management (Starts completely clean now)
        self.playlists = {}
        self.current_playlist_videos = [] # Holds (url, title) lists for active playlist view
        self.active_playback_source = "search" # "search" or "playlist"
        self.load_playlists()


        ###///////////////

        vbox_main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(vbox_main)
        # 1. Top section: Search Bar Components
        self.search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        vbox_main.pack_start(self.search_box, False, False, 0)
        
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text(_("Type song title or artist..."))
        self.entry.connect("activate", self.on_search_submitted)
        self.search_box.pack_start(self.entry, True, True, 0)
        
        # Clear Button implementation with native icon
        self.clear_button = Gtk.Button()
        btn_clear_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_clear = Gtk.Image.new_from_icon_name("edit-clear", Gtk.IconSize.BUTTON)
        lbl_clear = Gtk.Label(label=_("Clear"))
        btn_clear_box.pack_start(img_clear, False, False, 0)
        btn_clear_box.pack_start(lbl_clear, False, False, 0)
        self.clear_button.add(btn_clear_box)
        self.clear_button.connect("clicked", self.on_clear_clicked)
        self.search_box.pack_start(self.clear_button, False, False, 0)

        # Visual loading spinner
        self.spinner = Gtk.Spinner()
        self.search_box.pack_start(self.spinner, False, False, 4)
        
        # Search button initialization with native icon
        self.search_button = Gtk.Button()
        btn_search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_search = Gtk.Image.new_from_icon_name("edit-find", Gtk.IconSize.BUTTON)
        lbl_search = Gtk.Label(label=_("Search"))
        btn_search_box.pack_start(img_search, False, False, 0)
        btn_search_box.pack_start(lbl_search, False, False, 0)
        self.search_button.add(btn_search_box)
        self.search_button.connect("clicked", self.on_search_submitted)
        self.search_box.pack_start(self.search_button, False, False, 0)

        # 2. Middle section: Content Display Splitter (3 Columns Layout)
        self.hbox_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        vbox_main.pack_start(self.hbox_content, True, True, 0)
        # COLUMN A: Search Results TreeView
        vbox_search_results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.hbox_content.pack_start(vbox_search_results, False, False, 0)

        self.scroll_window = Gtk.ScrolledWindow()
        self.scroll_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scroll_window.set_size_request(400, -1)
        vbox_search_results.pack_start(self.scroll_window, True, True, 0)
        
        self.list_store = Gtk.ListStore(str, str, str)
        self.tree_view = Gtk.TreeView(model=self.list_store)
        self.scroll_window.add(self.tree_view)
        
        # Track last click interaction focus states
        self.tree_view.connect("button-press-event", lambda w, e: setattr(self, 'last_clicked_view', 'search') or False)

        # Connect exclusively to drag-end to let GTK handle visual focus shifts natively without jumping back
        self.tree_view.set_reorderable(True)
        self.tree_view.connect("drag-end", self.on_search_drag_end)
        
        self.tree_view.set_enable_search(False)
        self.tree_view.connect("row-activated", self.on_search_row_double_clicked)

        col_num = Gtk.TreeViewColumn("#", Gtk.CellRendererText(), text=0)
        col_num.set_min_width(35)
        self.tree_view.append_column(col_num)
        
        col_title = Gtk.TreeViewColumn(_("Search Results"), Gtk.CellRendererText(), text=1)
        col_title.set_expand(True)
        self.tree_view.append_column(col_title)

        # Context action to append to active list with a native system icon
        self.add_to_playlist_btn = Gtk.Button()
        btn_add_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_add = Gtk.Image.new_from_icon_name("list-add", Gtk.IconSize.BUTTON)
        lbl_add = Gtk.Label(label=_("Add Selected to Playlist"))
        btn_add_box.pack_start(img_add, False, False, 0)
        btn_add_box.pack_start(lbl_add, False, False, 0)
        self.add_to_playlist_btn.add(btn_add_box)
        
        self.add_to_playlist_btn.connect("clicked", self.on_add_to_playlist_clicked)
        self.add_to_playlist_btn.set_sensitive(False)
        vbox_search_results.pack_start(self.add_to_playlist_btn, False, False, 0)

        # COLUMN B: Embedded Video Pipeline Surface
        right_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.hbox_content.pack_start(right_vbox, True, True, 0)

        self.video_event_box = Gtk.EventBox()
        self.video_event_box.connect("button-press-event", self.on_video_clicked)
        right_vbox.pack_start(self.video_event_box, True, True, 0)

        self.video_overlay = Gtk.Overlay()
        self.video_event_box.add(self.video_overlay)

        ####///////////////////
       
        self.video_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.video_container.set_size_request(500, 380)
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

        # Inline Media Playback Controls with native icons
        self.video_control_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        right_vbox.pack_start(self.video_control_bar, False, False, 0)

        self.pause_button = Gtk.Button()
        self.btn_pause_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.img_pause = Gtk.Image.new_from_icon_name("media-playback-pause", Gtk.IconSize.BUTTON)
        self.lbl_pause = Gtk.Label(label=_("Pause"))
        self.btn_pause_box.pack_start(self.img_pause, False, False, 0)
        self.btn_pause_box.pack_start(self.lbl_pause, False, False, 0)
        self.pause_button.add(self.btn_pause_box)
        
        self.pause_button.connect("clicked", self.toggle_pause)
        self.pause_button.set_sensitive(False)
        self.video_control_bar.pack_start(self.pause_button, False, False, 0)

        volume_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_vol = Gtk.Image.new_from_icon_name("audio-volume-high", Gtk.IconSize.BUTTON)
        volume_box.pack_start(img_vol, False, False, 0)
        self.video_control_bar.pack_start(volume_box, False, False, 0)

        self.volume_slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume_slider.set_value(self.current_volume)
        self.volume_slider.set_size_request(120, -1)
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
        self.video_control_bar.pack_start(self.fullscreen_button, False, False, 0)

        # Autoplay Toggle Button Initialization
        self.autoplay_check = Gtk.CheckButton(label=_("Autoplay Next"))
        self.autoplay_check.set_active(self.autoplay_enabled)
        self.autoplay_check.connect("toggled", self.on_autoplay_toggled)
        self.video_control_bar.pack_start(self.autoplay_check, False, False, 0)
        # COLUMN C: Playlists Dashboard Panel
        self.playlist_panel_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.playlist_panel_vbox.set_size_request(320, -1)
        self.hbox_content.pack_start(self.playlist_panel_vbox, False, False, 0)

        playlist_selector_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.playlist_panel_vbox.pack_start(playlist_selector_hbox, False, False, 0)

        self.playlist_combo = Gtk.ComboBoxText()
        self.playlist_combo.connect("changed", self.on_playlist_combo_changed)
        playlist_selector_hbox.pack_start(self.playlist_combo, True, True, 0)

        new_playlist_btn = Gtk.Button(label=_("New List"))
        new_playlist_btn.connect("clicked", self.on_create_playlist_clicked)
        playlist_selector_hbox.pack_start(new_playlist_btn, False, False, 0)
        #############///

        self.playlist_scroll = Gtk.ScrolledWindow()
        self.playlist_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.playlist_panel_vbox.pack_start(self.playlist_scroll, True, True, 0)

        self.playlist_store = Gtk.ListStore(str, str) # Index, Video Title
        self.playlist_tree_view = Gtk.TreeView(model=self.playlist_store)
        self.playlist_tree_view.set_reorderable(True) # Allows reordering playlist rows via drag and drop
        # Connect row drop event to save the new updated sequence order into the JSON file
        self.playlist_store.connect("row-deleted", self.on_playlist_row_reordered)

        self.playlist_tree_view.connect("row-activated", self.on_playlist_row_double_clicked)
        
        # Track last click interaction focus states
        self.playlist_tree_view.connect("button-press-event", lambda w, e: setattr(self, 'last_clicked_view', 'playlist') or False)

        # CONNECT THIS NEW SIGNAL: Tracks selection changes inside the playlist View pipeline
        self.playlist_tree_view.get_selection().connect("changed", self.on_playlist_selection_changed)
        
        self.playlist_scroll.add(self.playlist_tree_view)

        col_pl_num = Gtk.TreeViewColumn("#", Gtk.CellRendererText(), text=0)
        col_pl_num.set_min_width(30)
        self.playlist_tree_view.append_column(col_pl_num)

        col_pl_title = Gtk.TreeViewColumn(_("Playlist Tracks"), Gtk.CellRendererText(), text=1)
        col_pl_title.set_expand(True)
        self.playlist_tree_view.append_column(col_pl_title)

        # Action Button A: Remove Selected Track using a native system icon
        self.remove_from_playlist_btn = Gtk.Button()
        btn_remove_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_remove = Gtk.Image.new_from_icon_name("list-remove", Gtk.IconSize.BUTTON)
        lbl_remove = Gtk.Label(label=_("Remove Selected Track"))
        btn_remove_box.pack_start(img_remove, False, False, 0)
        btn_remove_box.pack_start(lbl_remove, False, False, 0)
        self.remove_from_playlist_btn.add(btn_remove_box)
        
        self.remove_from_playlist_btn.connect("clicked", self.on_remove_track_clicked)
        self.playlist_panel_vbox.pack_start(self.remove_from_playlist_btn, False, False, 0)

        # Action Button B: Delete Entire Playlist Profile (Positioned perfectly under Remove Track)
        self.delete_playlist_btn = Gtk.Button()
        btn_del_list_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_del_list = Gtk.Image.new_from_icon_name("list-remove", Gtk.IconSize.BUTTON)
        lbl_del_list = Gtk.Label(label=_("Delete Playlist"))
        btn_del_list_box.pack_start(img_del_list, False, False, 0)
        btn_del_list_box.pack_start(lbl_del_list, False, False, 0)
        self.delete_playlist_btn.add(btn_del_list_box)
        
        self.delete_playlist_btn.connect("clicked", self.on_delete_playlist_clicked)
        self.playlist_panel_vbox.pack_start(self.delete_playlist_btn, False, False, 0)

        self.populate_playlist_combo()
        # 3. Bottom section: Controls & Status Frame Bar
        self.bottom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox_main.pack_start(self.bottom_box, False, False, 0)
        
        self.buttons_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.bottom_box.pack_start(self.buttons_row, False, False, 0)
        
        # Native System Icons for Control Row Buttons
        self.play_button = Gtk.Button()
        btn_play_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_play = Gtk.Image.new_from_icon_name("media-playback-start", Gtk.IconSize.BUTTON)
        lbl_play = Gtk.Label(label=_("Play"))
        btn_play_box.pack_start(img_play, False, False, 0)
        btn_play_box.pack_start(lbl_play, False, False, 0)
        self.play_button.add(btn_play_box)
        self.play_button.connect("clicked", self.on_play_button_clicked)
        self.play_button.set_sensitive(False)
        self.buttons_row.pack_start(self.play_button, False, False, 0)
        
        self.stop_button = Gtk.Button()
        btn_stop_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_stop = Gtk.Image.new_from_icon_name("media-playback-stop", Gtk.IconSize.BUTTON)
        lbl_stop = Gtk.Label(label=_("Stop"))
        btn_stop_box.pack_start(img_stop, False, False, 0)
        btn_stop_box.pack_start(lbl_stop, False, False, 0)
        self.stop_button.add(btn_stop_box)
        self.stop_button.connect("clicked", lambda b: self.stop_playback())
        self.stop_button.set_sensitive(False)
        self.buttons_row.pack_start(self.stop_button, False, False, 0)

        self.download_button = Gtk.Button()
        btn_dl_audio_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_dl_audio = Gtk.Image.new_from_icon_name("folder-download", Gtk.IconSize.BUTTON)
        lbl_dl_audio = Gtk.Label(label=_("Download MP3"))
        btn_dl_audio_box.pack_start(img_dl_audio, False, False, 0)
        btn_dl_audio_box.pack_start(lbl_dl_audio, False, False, 0)
        self.download_button.add(btn_dl_audio_box)
        self.download_button.connect("clicked", lambda b: self.start_download("mp3"))
        self.download_button.set_sensitive(False)
        self.buttons_row.pack_start(self.download_button, False, False, 0)

        self.download_video_button = Gtk.Button()
        btn_dl_video_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_dl_video = Gtk.Image.new_from_icon_name("video-x-generic", Gtk.IconSize.BUTTON)
        lbl_dl_video = Gtk.Label(label=_("Download MP4"))
        btn_dl_video_box.pack_start(img_dl_video, False, False, 0)
        btn_dl_video_box.pack_start(lbl_dl_video, False, False, 0)
        self.download_video_button.add(btn_dl_video_box)
        self.download_video_button.connect("clicked", lambda b: self.start_download("mp4"))
        self.download_video_button.set_sensitive(False)
        self.buttons_row.pack_start(self.download_video_button, False, False, 0)

        #####//////////////////////
        
        self.about_button = Gtk.Button()
        btn_about_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        img_about = Gtk.Image.new_from_icon_name("help-about", Gtk.IconSize.BUTTON)
        lbl_about = Gtk.Label(label=_("About"))
        btn_about_box.pack_start(img_about, False, False, 0)
        btn_about_box.pack_start(lbl_about, False, False, 0)
        self.about_button.add(btn_about_box)
        self.about_button.connect("clicked", self.show_about_dialog)
        self.buttons_row.pack_start(self.about_button, False, False, 0)
        
        # Initialize and build the Status Bar Layout Pipes
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
        
        # Pack components into status structures sequentially
        self.status_box.pack_start(self.status_image, False, False, 0)
        self.status_box.pack_start(self.status_label, True, True, 0)
        self.status_frame.add(self.status_box)

        # Connect Core Window Event Signals
        self.connect("key-release-event", self.on_key_release)
        self.connect("destroy", self.on_destroy)
    # --- PLAYLIST CORE BACKEND LOGIC ---
    def load_playlists(self):
        """Read localized profiles inside persistent standard userspace directories."""
        if not os.path.exists(CONFIG_DIR):
            os.makedirs(CONFIG_DIR, exist_ok=True)
        if os.path.exists(PLAYLISTS_FILE):
            try:
                with open(PLAYLISTS_FILE, 'r', encoding='utf-8') as f:
                    self.playlists = json.load(f)
            except Exception:
                self.playlists = {}
        else:
            self.playlists = {} # Starts completely empty, no "Default List" hardcoded entry written
            
    def on_playlist_selection_changed(self, selection):
        """Enables playback and download controls when a playlist item is selected."""
        model, treeiter = selection.get_selected()
        if treeiter is not None:
            self.play_button.set_sensitive(True)
            self.download_button.set_sensitive(True)
            self.download_video_button.set_sensitive(True)

    def save_playlists(self):
        """Write profiles structural dumps down to local storage blocks."""
        try:
            with open(PLAYLISTS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.playlists, f, ensure_ascii=False, indent=4)
        except Exception: pass

    def populate_playlist_combo(self):
        self.playlist_combo.disconnect_by_func(self.on_playlist_combo_changed)
        self.playlist_combo.remove_all()
        for name in self.playlists.keys():
            self.playlist_combo.append_text(name)
        if self.playlists:
            self.playlist_combo.set_active(0)
        else:
            self.playlist_store.clear() # Clear TreeView if no lists are left
        self.playlist_combo.connect("changed", self.on_playlist_combo_changed)
        self.refresh_playlist_ui_view()
        
    def refresh_playlist_ui_view(self):
        self.playlist_store.clear()
        self.current_playlist_videos = []
        active_list_name = self.playlist_combo.get_active_text()
        if active_list_name and active_list_name in self.playlists:
            self.current_playlist_videos = self.playlists[active_list_name]
            for idx, item in enumerate(self.current_playlist_videos):
                self.playlist_store.append([str(idx + 1), item["title"]])

    def on_playlist_row_reordered(self, model, path):
        """Triggered automatically when a playlist row is reordered to save database changes and lock running track focus."""
        active_list_name = self.playlist_combo.get_active_text()
        if not active_list_name or active_list_name not in self.playlists:
            return
    #---###########################

        new_ordered_list = []
        for row in self.playlist_store:
            title_on_screen = row
            for item in self.playlists[active_list_name]:
                if item["title"] == title_on_screen:
                    new_ordered_list.append(item)
                    break

        if len(new_ordered_list) == len(self.playlists[active_list_name]):
            self.playlists[active_list_name] = new_ordered_list
            self.current_playlist_videos = new_ordered_list
            self.save_playlists()
            
            # Temporarily disconnect listener to rewrite index indices cleanly
            self.playlist_store.disconnect_by_func(self.on_playlist_row_reordered)
            
            playing_iter = None
            for idx, row in enumerate(self.playlist_store):
                row = str(idx + 1)
                # Track down if this specific row is the video currently active in the mpv core pipeline
                if self.active_playback_source == "playlist" and self.current_playlist_videos[idx]["url"] == self.current_url:
                    playing_iter = row.iter

            self.playlist_store.connect("row-deleted", self.on_playlist_row_reordered)
            
            # Force restore visual high-light metrics back onto the actively playing track node safely
            if playing_iter:
                self.playlist_tree_view.get_selection().select_iter(playing_iter)

    def on_search_drag_end(self, tree_view, context):
        """Triggered automatically after the native GTK drag operation completes to sync URLs and fix numbering without losing focus."""
        old_titles_map = []
        for idx in range(len(self.video_urls)):
            if idx < len(self.list_store):
                try:
                    orig_iter = self.list_store.get_iter(Gtk.TreePath.new_from_indices([idx]))
                    old_titles_map.append({"url": self.video_urls[idx], "title": self.list_store.get_value(orig_iter, 1)})
                except Exception: pass

        new_ordered_urls = []
        for row in self.list_store:
            title_on_screen = row
            for item in old_titles_map:
                if item["title"] == title_on_screen:
                    new_ordered_urls.append(item["url"])
                    break

        if len(new_ordered_urls) == len(self.video_urls):
            self.video_urls = new_ordered_urls

        # Defer numbering rewrite to next idle cycle to preserve native selection tracking updates
        GLib.idle_add(lambda: self.normalize_search_row_numbers())

    def normalize_search_row_numbers(self):
        """Enforces clean sequential 1,2,3... layout numbers without affecting active selection tracks."""
        for idx, row in enumerate(self.list_store):
            self.list_store[row.iter] = str(idx + 1)
        return False

    def on_delete_playlist_clicked(self, button):
        """Displays a confirmation dialog and permanently purges the selected playlist from local storage."""
        active_list_name = self.playlist_combo.get_active_text()
        
        if not active_list_name:
            self.update_status(_("No active playlist selected to delete."), "red", "dialog-warning")
            return

        dialog = Gtk.MessageDialog(
            transient_for=self, 
            flags=0, 
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL, 
            text=_("Delete Playlist Confirmation")
        )
        dialog.format_secondary_text(f"{_('Are you absolutely sure you want to permanently delete')} '{active_list_name}'?")
        dialog.show_all()
        
        response = dialog.run()
        dialog.destroy()
        
        if response == Gtk.ResponseType.OK:
            if active_list_name in self.playlists:
                del self.playlists[active_list_name]
                
            self.save_playlists()
            self.populate_playlist_combo()
            
            # Clean text status layout update using standard safe icon tokens
            success_msg = f"[-] {_('Playlist')} '{active_list_name}' {_('deleted successfully.')}"
            self.update_status(success_msg, "green", "dialog-information")

    # --- PLAYLIST UI ACTIONS ---
    def on_create_playlist_clicked(self, button):
        dialog = Gtk.MessageDialog(transient_for=self, flags=0, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.OK_CANCEL, text=_("Create New Playlist"))
        dialog.format_secondary_text(_("Enter a unique name for your custom playlist:"))
        box = dialog.get_content_area()
        entry = Gtk.Entry()
        entry.set_margin_top(10)
        box.add(entry)
        dialog.show_all()
        
        response = dialog.run()
        name_text = entry.get_text().strip()
        dialog.destroy()
        
        if response == Gtk.ResponseType.OK and name_text:
            if name_text not in self.playlists:
                self.playlists[name_text] = []
                self.save_playlists()
                self.populate_playlist_combo()
                
                # Automatically focus and activate the newly created custom playlist profile
                model = self.playlist_combo.get_model()
                for idx in range(len(model)):
                    if model[idx][0] == name_text:
                        self.playlist_combo.set_active(idx)
                        break
            else:
                self.update_status(_("Playlist name already exists!"), "red", "dialog-warning")

           
    # --- PLAYLIST UI ACTIONS ---
    def on_create_playlist_clicked(self, button):
        dialog = Gtk.MessageDialog(transient_for=self, flags=0, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.OK_CANCEL, text=_("Create New Playlist"))
        dialog.format_secondary_text(_("Enter a unique name for your custom playlist:"))
        box = dialog.get_content_area()
        entry = Gtk.Entry()
        entry.set_margin_top(10)
        box.add(entry)
        dialog.show_all()
        
        response = dialog.run()
        name_text = entry.get_text().strip()
        dialog.destroy()
        
        if response == Gtk.ResponseType.OK and name_text:
            if name_text not in self.playlists:
                self.playlists[name_text] = []
                self.save_playlists()
                self.populate_playlist_combo()
                model = self.playlist_combo.get_model()
                for idx in range(len(model)):
                    if model[idx] == name_text:
                        self.playlist_combo.set_active(idx)
                        break
            else:
                self.update_status(_("Playlist name already exists!"), "red", "dialog-warning")


    ############################/

    def on_playlist_combo_changed(self, combo):
        self.refresh_playlist_ui_view()

    def on_add_to_playlist_clicked(self, button):
        """Appends the active search video item to the selected playlist profile array and updates status with the song title."""
        url, title = self.get_selected_search_url()
        active_list_name = self.playlist_combo.get_active_text()
        if not active_list_name:
            self.update_status(_("Create or select a playlist first!"), "red", "dialog-warning")
            return
        if url and title:
            if any(v["url"] == url for v in self.playlists[active_list_name]):
                self.update_status(_("Track already exists in this list."), "red", "dialog-warning")
                return
            self.playlists[active_list_name].append({"url": url, "title": title})
            self.save_playlists()
            self.refresh_playlist_ui_view()
            
            # FIXED STRING: Replaced the non-standard emoji layout token with a clean native standard text identifier
            success_msg = f"[+] '{title[:30]}' {_('added to')} {active_list_name}!"
            self.update_status(success_msg, "green", "dialog-information")

    def on_remove_track_clicked(self, button):
        selection = self.playlist_tree_view.get_selection()
        model, treeiter = selection.get_selected()
        active_list_name = self.playlist_combo.get_active_text()
        if treeiter is not None and active_list_name:
            row_num_str = model.get_value(treeiter, 0)
            idx = int(row_num_str) - 1
            if 0 <= idx < len(self.playlists[active_list_name]):
                del self.playlists[active_list_name][idx]
                self.save_playlists()
                self.refresh_playlist_ui_view()
                self.update_status(_("Track removed from list."), icon_name="user-trash")
    # --- BASE INHERITED MEDIA LOGIC WRAPPERS ---
    def update_status(self, text, color="#3584e4", icon_name="dialog-information"):
        """Safe update of the UI status bar from background threads by forcefully recreating the image widget asset."""
        def idle_status_sync():
            # 1. Remove the old image widget container entirely to clear the rendering buffer
            if hasattr(self, 'status_image') and self.status_image in self.status_box.get_children():
                self.status_box.remove(self.status_image)
            
            # 2. Re-create a fresh standalone hardware image instance using the requested system icon name
            self.status_image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
            
            # 3. Pack the fresh instance back into the start position of the status layout pipe
            self.status_box.pack_start(self.status_image, False, False, 0)
            self.status_box.reorder_child(self.status_image, 0)
            
            # 4. Render text update updates securely onto the markup label screen node
            self.status_label.set_markup(f"<span foreground='{color}'><i>{text}</i></span>")
            self.status_box.show_all()

        GLib.idle_add(idle_status_sync)

    def on_clear_clicked(self, button):
        self.entry.set_text("")
        self.entry.grab_focus()
        self.update_status(_("Cleaning yt-dlp cache storage..."), icon_name="user-trash")
        threading.Thread(target=self.clear_ytdlp_cache_thread, daemon=True).start()

    def clear_ytdlp_cache_thread(self):
        try:
            subprocess.run(["yt-dlp", "--rm-cache-dir"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.update_status(_("Cache cleared successfully. System ready."), "green", icon_name="emblem-ok")
        except Exception:
            self.update_status(_("Failed to purge cache data."), "red", icon_name="dialog-warning")

    def on_autoplay_toggled(self, button):
        self.autoplay_enabled = button.get_active()

    def send_mpv_ipc_command(self, cmd_list):
        if self.mpv_process and self.mpv_process.poll() is None:
            if os.path.exists(self.mpv_socket):
                try:
                    payload = json.dumps({"command": cmd_list}) + "\n"
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    client.connect(self.mpv_socket)
                    client.sendall(payload.encode('utf-8'))
                    client.close()
                except Exception: pass
    #########################

    def send_mpv_ipc_query(self, cmd_list):
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
        self.spinner.start()
        
        self.list_store.clear()
        self.video_urls = []
        self.stop_playback()
        self.update_status(_("Searching YouTube (fetching 150 items)..."), icon_name="edit-find")
        threading.Thread(target=self.fetch_youtube_results, args=(user_input,), daemon=True).start()

    def fetch_youtube_results(self, phrase):
        with self.search_lock:
            try:
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
        count = 1
        for url, title in results:
            self.video_urls.append(url)
            self.list_store.append([str(count), title, "YouTube"])
            count += 1
            
        self.spinner.stop()
        self.update_status(f"{_('Displaying')} {len(self.video_urls)} {_('unique results.')}", icon_name="emblem-ok")
        self.play_button.set_sensitive(True)
        self.add_to_playlist_btn.set_sensitive(True)
        self.download_button.set_sensitive(True)
        self.download_video_button.set_sensitive(True)
        self.search_button.set_sensitive(True)
        self.entry.set_sensitive(True)
        return False
    def get_selected_search_url(self):
        selection = self.tree_view.get_selection()
        model, treeiter = selection.get_selected()
        if treeiter is not None:
            row_num_str = model.get_value(treeiter, 0)
            title = model.get_value(treeiter, 1)
            index = int(row_num_str) - 1
            if 0 <= index < len(self.video_urls): 
                return self.video_urls[index], title
        return None, None
    ############################

    def get_selected_playlist_url(self):
        selection = self.playlist_tree_view.get_selection()
        model, treeiter = selection.get_selected()
        if treeiter is not None:
            row_num_str = model.get_value(treeiter, 0)
            index = int(row_num_str) - 1
            if 0 <= index < len(self.current_playlist_videos):
                item = self.current_playlist_videos[index]
                return item["url"], item["title"]
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
        self.socket.set_size_request(500, 380)
        self.video_container.pack_start(self.socket, True, True, 0)
        self.video_container.show_all()
        self.socket.realize()
        
        socket_id = self.socket.get_id()
        self.update_status(f"{_('Loading video:')} {title}", icon_name="media-seek-forward")
        self.stop_button.set_sensitive(True)
        self.fullscreen_button.set_sensitive(True)
        self.buttons_row.set_focus_child(self.play_button)
        self.pause_button.set_sensitive(True)
        
        self.img_pause.set_from_icon_name("media-playback-pause", Gtk.IconSize.BUTTON)
        self.lbl_pause.set_text(_("Pause"))
        self.is_paused = False

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
            self.ipc_thread_active = True
            threading.Thread(target=self.monitor_mpv_playback, daemon=True).start()
        except Exception:
            self.update_status(_("Failed to start playback."), "red", icon_name="dialog-error")
        return False

    def monitor_mpv_playback(self):
        while self.ipc_thread_active:
            if not self.mpv_process or self.mpv_process.poll() is not None:
                break
            
            res = self.send_mpv_ipc_query(["get_property", "eof-reached"])
            if res and isinstance(res, dict):
                if res.get("error") == "success" and res.get("data") is True:
                    if self.is_fullscreen:
                        GLib.idle_add(self.toggle_fullscreen)
                    
                    if self.autoplay_enabled:
                        GLib.idle_add(self.play_next_video)
                    break
                    
            time.sleep(0.4)
    #---###########################


    def play_next_video(self):
        """Finds the finished track by URL and safely plays the next row, ignoring UI selection shifts."""
        if self.active_playback_source == "search":
            model = self.list_store
            tree_view = self.tree_view
            urls_list = self.video_urls
            
            # Find the index of the currently finished video inside the memory list
            try:
                current_idx = urls_list.index(self.current_url)
            except ValueError:
                return # Current URL not found, abort autoplay safely

            next_idx = current_idx + 1
            if 0 <= next_idx < len(urls_list):
                # Look up the next title from the screen tree view row layout safely
                treeiter = model.get_iter(Gtk.TreePath.new_from_indices([next_idx]))
                title = model.get_value(treeiter, 1)
                url = urls_list[next_idx]
                
                # Move selection visually to match what is playing next
                tree_view.get_selection().select_iter(treeiter)
                
                self.stop_playback()
                self.active_playback_source = "search"
                threading.Thread(target=self.play_video, args=(url, title), daemon=True).start()
        else:
            model = self.playlist_store
            tree_view = self.playlist_tree_view
            
            # Find the index of the currently finished video inside the playlist dataset
            current_idx = -1
            for idx, item in enumerate(self.current_playlist_videos):
                if item["url"] == self.current_url:
                    current_idx = idx
                    break
                    
            if current_idx == -1:
                return # Track not found in active playlist layout state

            next_idx = current_idx + 1
            if 0 <= next_idx < len(self.current_playlist_videos):
                # Look up the next structural target block data tags
                treeiter = model.get_iter(Gtk.TreePath.new_from_indices([next_idx]))
                title = self.current_playlist_videos[next_idx]["title"]
                url = self.current_playlist_videos[next_idx]["url"]
                
                # Move selection visually to match what is playing next
                tree_view.get_selection().select_iter(treeiter)
                
                self.stop_playback()
                self.active_playback_source = "playlist"
                threading.Thread(target=self.play_video, args=(url, title), daemon=True).start()

    def on_volume_changed(self, scroll):
        self.current_volume = self.volume_slider.get_value()
        self.send_mpv_ipc_command(["set_property", "volume", int(self.current_volume)])

    def toggle_pause(self, button):
        if self.mpv_process and self.mpv_process.poll() is None:
            self.is_paused = not self.is_paused
            self.send_mpv_ipc_command(["set_property", "pause", self.is_paused])
            if self.is_paused:
                self.img_pause.set_from_icon_name("media-playback-start", Gtk.IconSize.BUTTON)
                self.lbl_pause.set_text(_("Resume"))
                self.update_status(_("Playback paused."), icon_name="media-playback-pause")
            else:
                self.img_pause.set_from_icon_name("media-playback-pause", Gtk.IconSize.BUTTON)
                self.lbl_pause.set_text(_("Pause"))
                self.update_status(f"{_('Playing:')} {self.current_title}", icon_name="media-playback-start")

    def toggle_fullscreen(self):
        if not self.is_fullscreen:
            self.search_box.hide()
            self.scroll_window.hide()
            self.add_to_playlist_btn.hide()
            self.video_control_bar.hide()
            self.playlist_panel_vbox.hide()
            self.buttons_row.hide()
            
            self.fullscreen() 
            self.is_fullscreen = True
            self.fs_info_label.show() 
        else:
            self.fs_info_label.hide() 
            self.search_box.show()
            self.scroll_window.show()
            self.add_to_playlist_btn.show()
            self.video_control_bar.show()
            self.playlist_panel_vbox.show()
            self.buttons_row.show()
            
            self.unfullscreen() 
            self.is_fullscreen = False
    ############################

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
        about.set_copyright("Copyright © 2026 Dimitris Tzemos <dijemos@gmail.com>")
        about.set_comments(_("An advanced, thread-safe embedded player for YouTube videos and music with standalone download support."))
        about.set_website("https://github.com")
        about.set_authors(["Dimitris Tzemos <dijemos@gmail.com>"])
        
        if self.app_pixbuf: about.set_logo(self.app_pixbuf)
        about.set_license_type(Gtk.License.GPL_3_0)
        about.connect("response", lambda d, r: d.destroy())
        about.show()

    def start_download(self, file_type):
        if self.active_playback_source == "search":
            url, title = self.get_selected_search_url()
        else:
            url, title = self.get_selected_playlist_url()

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
    #////////////////

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
        
        # Keep Play active if there is any track loaded in the current workspace arrays
        if self.video_urls:
            GLib.idle_add(self.play_button.set_sensitive, True)
        else:
            GLib.idle_add(self.play_button.set_sensitive, False)
            
        self.img_pause.set_from_icon_name("media-playback-pause", Gtk.IconSize.BUTTON)
        self.lbl_pause.set_text(_("Pause"))
        self.is_paused = False
        self.update_status(_("Ready."), icon_name="dialog-information")

    def on_play_button_clicked(self, button):
        """Checks which side was clicked last by the user and safely streams the corresponding track."""
        url_s, title_s = self.get_selected_search_url()
        url_p, title_p = self.get_selected_playlist_url()
        
        # Scenario 1: User target is the Playlist side based on the last mouse click interaction
        if self.last_clicked_view == "playlist" and url_p:
            self.stop_playback()
            self.active_playback_source = "playlist"
            threading.Thread(target=self.play_video, args=(url_p, title_p), daemon=True).start()
            return

        # Scenario 2: User target is the Search side based on the last mouse click interaction
        if self.last_clicked_view == "search" and url_s:
            self.stop_playback()
            self.active_playback_source = "search"
            threading.Thread(target=self.play_video, args=(url_s, title_s), daemon=True).start()
            return
            
        # Fallback: If the last clicked view has no selection, check the alternative side safely
        if url_p:
            self.stop_playback()
            self.active_playback_source = "playlist"
            threading.Thread(target=self.play_video, args=(url_p, title_p), daemon=True).start()
        elif url_s:
            self.stop_playback()
            self.active_playback_source = "search"
            threading.Thread(target=self.play_video, args=(url_s, title_s), daemon=True).start()

    def on_search_row_double_clicked(self, tree_view, path, column):
        url, title = self.get_selected_search_url()
        if url: 
            self.stop_playback() 
            self.active_playback_source = "search"
            threading.Thread(target=self.play_video, args=(url, title), daemon=True).start()

    def on_playlist_row_double_clicked(self, tree_view, path, column):
        url, title = self.get_selected_playlist_url()
        if url:
            self.stop_playback()
            self.active_playback_source = "playlist"
            threading.Thread(target=self.play_video, args=(url, title), daemon=True).start()

    def on_destroy(self, widget):
        self.stop_playback()
        time.sleep(0.1) 
        Gtk.main_quit()

if __name__ == "__main__":
    win = YouTubeInsidePlayer()
    win.show_all()
    Gtk.main()
