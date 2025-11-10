# Version 94
# ============================================================================
# VERSION UPDATE CHECKLIST - READ THIS BEFORE EDITING!
# ============================================================================
# This is the APP VERSION (UI), NOT the CDEM Agent version!
# 
# When you update this file, increment the version number above AND:
#   1. It will AUTO-UPDATE in the window title bar at top (via get_app_version())
#   2. It will AUTO-UPDATE in App Logs (via get_app_version())
#
# CDEM Agent Version (separate!):
#   - Stored in: src/agents/cdem_agent.py (line 1: "# Version: XX")
#   - Displayed on: Dashboard tab (reads from cdem_agent.py via get_agent_version())
#   - Only update when you edit the TRADING AGENT, not the UI
# ============================================================================
import tkinter as tk
from tkinter import ttk, messagebox
import json
import os
import subprocess
import threading
import time
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import queue  # For thread-safe logging
from datetime import datetime, timedelta
import re  # For parsing logs

try:
    import ctypes  # For Ctrl+C signal
    import os
    import signal
    import win32gui
    import win32con
    import win32api
except ImportError:
    print("pywin32 not installed; install with 'pip install pywin32' for terminal embedding.")
    win32api = None
from dotenv import load_dotenv  # Added for .env

try:
    import pystray
    from PIL import Image
except ImportError:
    pystray = None
    Image = None

import finnhub  # For stock names

# Local imports
from utils.ansi_parser import ANSIParser
from config.tooltips import TOOLTIP_DESCRIPTIONS
from utils.window_position import WindowPositionManager

plt.style.use("dark_background")  # Added for Matplotlib dark mode

# Constants
CONFIG_PATH = "config.json"
PREFERENCES_PATH = "preferences.json"  # New for settings
LOG_PATH = os.path.join("logs", "terminal_logs.txt")  # Terminal logs from agent
APP_LOG_PATH = os.path.join("logs", "app_logs.txt")  # App internal event logs
GROK_LOG_PATH = os.path.join("logs", "grok_logs.json")  # Grok LLM interaction logs
SENTIMENT_CSV = os.path.join("src", "data", "cdem", "sentiment_history.csv")
PORTFOLIO_CSV = os.path.join("src", "data", "cdem", "portfolio.csv")
AGENT_SCRIPT = os.path.join("src", "agents", "cdem_agent.py")

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)


class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.widget.bind("<Enter>", self.show)
        self.widget.bind("<Leave>", self.hide)
        self.tip = None

    def show(self, event=None):
        x = y = 0
        x, y, _, _ = self.widget.bbox("insert")
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 20
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            self.tip,
            text=self.text,
            background="gray20",
            foreground="white",
            relief="solid",
            borderwidth=1,
            padx=5,
            pady=3,
            wraplength=300,
            justify="left",
        )
        label.pack()

    def hide(self, event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class FileWatcherHandler(FileSystemEventHandler):
    def __init__(self, callback):
        self.callback = callback

    def on_modified(self, event):
        if event.src_path.endswith(".csv"):
            self.callback()


class GrokLogWatcherHandler(FileSystemEventHandler):
    def __init__(self, callback):
        self.callback = callback

    def on_modified(self, event):
        if event.src_path.endswith("grok_logs.json"):
            self.callback()


class CDEMApp:
    def __init__(self, root):
        load_dotenv()  # Added: Load .env for API keys

        self.root = root
        self.root.overrideredirect(True)  # Borderless window
        
        # Initialize window position manager and restore last position
        # Use absolute path to ensure file is saved in the app directory
        window_pos_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "window_position.json")
        self.window_pos_manager = WindowPositionManager(config_file=window_pos_file)
        self.window_pos_manager.apply_position(self.root)
        
        # Debounced save - only save after user stops moving/resizing
        self._position_save_timer = None
        self._last_saved_position = None
        self.root.bind("<Configure>", self.on_window_configure)
        
        self.root.configure(bg="black")  # Dark mode
        try:
            self.root.iconphoto(False, tk.PhotoImage(file="icon.png"))
        except Exception as e:
            print(f"Failed to load icon: {e}")

        # Add to taskbar
        try:
            hwnd = self.root.winfo_id()
            extended_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, extended_style | win32con.WS_EX_APPWINDOW)
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        except Exception as e:
            print(f"Taskbar integration failed: {e}")

        # Dark mode style (Moved up)
        self.style = ttk.Style()
        self.style.theme_use('clam')  # Use clam theme for better dark mode customization
        self.style.configure(".", background="black", foreground="white")
        self.style.configure("TFrame", background="black")
        self.style.configure("TLabel", background="black", foreground="white")
        self.style.configure("TButton", background="gray20", foreground="white", relief="flat")
        self.style.map("TButton", background=[("active", "gray30")])
        self.style.configure("TCheckbutton", background="black", foreground="white")
        self.style.configure(
            "TEntry",
            fieldbackground="gray20",
            foreground="white",
            insertbackground="white",
            borderwidth=0,
            relief="flat",
        )
        self.style.configure(
            "TSpinbox",
            fieldbackground="gray20",
            foreground="white",
            insertbackground="white",
            borderwidth=0,
            relief="flat",
        )
        self.style.configure("TNotebook", background="black")
        self.style.configure("TNotebook.Tab", background="gray20", foreground="white")
        self.style.map(
            "TNotebook.Tab", background=[("selected", "gray30")], foreground=[("selected", "white")]
        )

        # Scrollbar style for alt theme (applies to all ttk scrollbars)
        self.style.configure("Vertical.TScrollbar", 
                           background="gray20", 
                           troughcolor="black", 
                           arrowcolor="white")
        self.style.configure("Horizontal.TScrollbar", 
                           background="gray20", 
                           troughcolor="black", 
                           arrowcolor="white")

        # Config tab custom styles (defined early to override alt theme defaults)
        # Dark Entry style
        self.style.map('Dark.TEntry',
                       fieldbackground=[('readonly', 'black'), ('!readonly', 'black')],
                       foreground=[('readonly', 'white'), ('!readonly', 'white')])
        self.style.configure('Dark.TEntry',
                             fieldbackground='black',
                             foreground='white',
                             insertcolor='white',
                             bordercolor='gray40',
                             lightcolor='black',
                             darkcolor='black')
        
        # Dark Spinbox style
        self.style.map('Dark.TSpinbox',
                       fieldbackground=[('readonly', 'black'), ('!readonly', 'black')],
                       foreground=[('readonly', 'white'), ('!readonly', 'white')])
        self.style.configure('Dark.TSpinbox',
                             fieldbackground='black',
                             foreground='white',
                             insertcolor='white',
                             arrowcolor='white',
                             bordercolor='gray40',
                             lightcolor='black',
                             darkcolor='black')
        
        # Dark Combobox style
        self.style.map('Dark.TCombobox', 
                        fieldbackground=[('readonly', 'black')],
                        selectbackground=[('readonly', 'black')],
                        selectforeground=[('readonly', 'white')])
        self.style.configure('Dark.TCombobox',
                             fieldbackground='black',
                             background='black',
                             foreground='white',
                             arrowcolor='white',
                             bordercolor='gray40',
                             lightcolor='black',
                             darkcolor='black')

        # Treeview style (comprehensive dark mode with clam theme)
        self.style.configure("Dark.Treeview", 
                           background="black", 
                           foreground="white", 
                           fieldbackground="black",
                           bordercolor="black",
                           lightcolor="black",
                           darkcolor="black")
        self.style.configure("Dark.Treeview.Heading", background="gray30", foreground="white")
        # Note: No style.map for background - allows tag-based coloring
        self.style.map("Dark.Treeview.Heading", background=[("active", "gray40")])

        # Title frame style
        self.style.configure("Title.TFrame", background="black")

        # Red button style for min/close
        self.style.configure("Red.TButton", foreground="red", background="gray20")
        self.style.map("Red.TButton", foreground=[("active", "darkred")])

        # Bold red button style
        self.style.configure("Bold.Red.TButton", font=("Arial", 10, "bold"))

        # Custom title bar (Now after style setup)
        self.title_bar = ttk.Frame(self.root, height=30, relief="raised", style="Title.TFrame")
        self.title_bar.pack(fill="x")

        # IMPORTANT: This displays the APP version (from line 1 of app.py)
        # NOT the CDEM agent version (which is shown on dashboard)
        app_version = self.get_app_version()
        title_label = ttk.Label(
            self.title_bar, text=f"Backslash Robotics™ CDEM Agent Controller - Version {app_version}", foreground="white"
        )
        title_label.pack(side="left", padx=10)

        # Close button (swapped position)
        close_button = tk.Button(
            self.title_bar,
            text="X",
            bg="gray20",
            fg="white",
            command=self.close_app,
            width=2,
            font=("Arial", 10, "bold"),
        )
        close_button.pack(side="right")
        
        # Bind window close protocol to save position
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)

        # Toggle button (fullscreen/small window)
        self.toggle_window_button = tk.Button(
            self.title_bar,
            text="□",
            bg="gray20",
            fg="white",
            command=self.toggle_window_size,
            width=2,
            font=("Arial", 10, "bold"),
        )
        self.toggle_window_button.pack(side="right")
        
        # Split screen buttons (up, down, left, right)
        up_button = tk.Button(
            self.title_bar,
            text="↑",
            bg="gray20",
            fg="white",
            command=self.snap_to_top,
            width=2,
            font=("Arial", 10, "bold"),
        )
        up_button.pack(side="right")
        
        down_button = tk.Button(
            self.title_bar,
            text="↓",
            bg="gray20",
            fg="white",
            command=self.snap_to_bottom,
            width=2,
            font=("Arial", 10, "bold"),
        )
        down_button.pack(side="right")
        
        left_button = tk.Button(
            self.title_bar,
            text="←",
            bg="gray20",
            fg="white",
            command=self.snap_to_left,
            width=2,
            font=("Arial", 10, "bold"),
        )
        left_button.pack(side="right")
        
        right_button = tk.Button(
            self.title_bar,
            text="→",
            bg="gray20",
            fg="white",
            command=self.snap_to_right,
            width=2,
            font=("Arial", 10, "bold"),
        )
        right_button.pack(side="right")

        # Resizing and moving functionality
        self.action = ""
        self.is_fullscreen = False
        self.normal_geometry = None
        self.root.bind("<Motion>", self.change_cursor)
        self.root.bind("<ButtonPress-1>", self.start_action)
        self.root.bind("<B1-Motion>", self.perform_action)
        
        # Bind title bar specifically for dragging (overrides general binding)
        self.title_bar.bind("<ButtonPress-1>", self.start_titlebar_drag)
        self.title_bar.bind("<B1-Motion>", self.perform_titlebar_drag)
        title_label.bind("<ButtonPress-1>", self.start_titlebar_drag)
        title_label.bind("<B1-Motion>", self.perform_titlebar_drag)

        # Tray icon for restore (if pystray installed)
        self.tray_icon = None
        if pystray and Image:
            icon_image = Image.open("icon.png")
            menu = pystray.Menu(pystray.MenuItem("Show", self.restore_app), pystray.MenuItem("Exit", self.root.destroy))
            self.tray_icon = pystray.Icon("CDEM", icon_image, "CDEM Agent", menu)
        else:
            print("Install 'pystray' and 'pillow' for system tray icon on minimize.")

        # Descriptions for tooltips (loaded from config)
        self.descriptions = TOOLTIP_DESCRIPTIONS

        # Agent process and logging
        self.agent_process = None
        self.log_queue = queue.Queue()  # For thread-safe logs
        self.all_logs = []  # Persist all log lines for parsing
        self.running = False
        self.blinking = False  # For status blink

        # Finnhub client for stock names
        self.finnhub_client = finnhub.Client(api_key=os.getenv("FINNHUB_API_KEY"))
        
        # Initialize Grok model factory once for reuse (optimization)
        self.grok_model = None

        # Load initial config and preferences
        self.config = self.load_config()
        self.preferences = self.load_preferences()

        # Setup UI (after title bar)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(expand=True, fill="both")

        self.setup_dashboard_tab()
        self.setup_config_tab()
        self.setup_visuals_tab()
        self.setup_terminal_logs_tab()  # Renamed from logs
        self.setup_grok_logs_tab()  # New tab
        self.setup_app_logs_tab()  # App event logs

        # Start file watcher for CSVs and grok_raw.txt
        self.start_file_watcher()

        # Auto-refresh visuals every 10s
        self.root.after(10000, self.refresh_visuals)
        
        # Start account balance updates (every 30s)
        self.root.after(1000, self.update_account_balances)

        # Auto-refresh dashboard table every 60s (threaded)
        self.root.after(5000, self.update_dashboard_table_threaded)

    def change_cursor(self, event):
        if self.is_fullscreen:
            self.root.config(cursor="")
            self.action = ""
            return
        
        x, y = event.x, event.y
        width, height = self.root.winfo_width(), self.root.winfo_height()
        edge = 10  # Larger hit zone for easier resizing
        corner = 20  # Corner detection zone (20x20 pixels in each corner)
        
        # Title bar (top 30px) is handled by specific bindings - don't set action here
        if y < 30:
            self.root.config(cursor="")
            self.action = ""
            return
        
        # Corner resizing (priority over edge resizing)
        # Corners are detected in corner x corner pixel squares
        if x < corner and y < 30 + corner:
            self.root.config(cursor="top_left_corner")
            self.action = "resize_nw"
        elif x > width - corner and y < 30 + corner:
            self.root.config(cursor="top_right_corner")
            self.action = "resize_ne"
        elif x < corner and y > height - corner:
            self.root.config(cursor="bottom_left_corner")
            self.action = "resize_sw"
        elif x > width - corner and y > height - corner:
            self.root.config(cursor="bottom_right_corner")
            self.action = "resize_se"
        # Edge resizing (only if not in a corner)
        elif x < edge:
            self.root.config(cursor="left_side")
            self.action = "resize_w"
        elif x > width - edge:
            self.root.config(cursor="right_side")
            self.action = "resize_e"
        elif y < edge + 30:  # Top edge starts after title bar (30px)
            self.root.config(cursor="top_side")
            self.action = "resize_n"
        elif y > height - edge:
            self.root.config(cursor="bottom_side")
            self.action = "resize_s"
        else:
            self.root.config(cursor="")
            self.action = ""

    def start_action(self, event):
        # Only handle resize actions (move is handled by title bar specific bindings)
        if self.action and "resize" in self.action:
            self.start_x = event.x_root
            self.start_y = event.y_root
            self.start_w = self.root.winfo_width()
            self.start_h = self.root.winfo_height()
            self.start_win_x = self.root.winfo_x()
            self.start_win_y = self.root.winfo_y()

    def perform_action(self, event):
        if not self.action or "resize" not in self.action:
            return
        dx = event.x_root - self.start_x
        dy = event.y_root - self.start_y
        if "resize" in self.action:
            new_x = self.start_win_x
            new_y = self.start_win_y
            new_w = self.start_w
            new_h = self.start_h
            if "n" in self.action:
                new_y += dy
                new_h -= dy
            if "s" in self.action:
                new_h += dy
            if "w" in self.action:
                new_x += dx
                new_w -= dx
            if "e" in self.action:
                new_w += dx
            new_w = max(new_w, 300)
            new_h = max(new_h, 200)
            self.root.geometry(f"{new_w}x{new_h}+{new_x}+{new_y}")
    
    def start_titlebar_drag(self, event):
        """Start dragging from title bar - only allows moving the window"""
        self.drag_start_x = event.x_root
        self.drag_start_y = event.y_root
        self.drag_win_x = self.root.winfo_x()
        self.drag_win_y = self.root.winfo_y()
    
    def perform_titlebar_drag(self, event):
        """Perform title bar drag - move window"""
        dx = event.x_root - self.drag_start_x
        dy = event.y_root - self.drag_start_y
        new_x = self.drag_win_x + dx
        new_y = self.drag_win_y + dy
        self.root.geometry(f"+{new_x}+{new_y}")

    def minimize_app(self):
        self.root.withdraw()
        if self.tray_icon:
            threading.Thread(target=self.tray_icon.run).start()

    def restore_app(self):
        self.root.deiconify()
        if self.tray_icon:
            self.tray_icon.stop()
    
    def toggle_fullscreen(self):
        """Toggle between fullscreen and normal window mode"""
        self.log_app_event("DEBUG", f"Toggle fullscreen (currently {'ON' if self.is_fullscreen else 'OFF'})")
        if self.is_fullscreen:
            # Restore to normal
            self.is_fullscreen = False
            self.root.overrideredirect(True)  # Keep borderless
            if self.normal_geometry:
                self.root.geometry(self.normal_geometry)
        else:
            # Go fullscreen
            self.is_fullscreen = True
            # Save current geometry
            self.normal_geometry = self.root.geometry()
            
            # Use Windows API to get the actual monitor the window is on
            if win32api:
                try:
                    # Get window handle
                    hwnd = self.root.winfo_id()
                    
                    # Get the monitor the window is currently on
                    monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
                    
                    # Get monitor info (includes work area and full monitor dimensions)
                    monitor_info = win32api.GetMonitorInfo(monitor)
                    
                    # Get the monitor's full dimensions
                    monitor_rect = monitor_info['Monitor']
                    monitor_x = monitor_rect[0]
                    monitor_y = monitor_rect[1]
                    monitor_width = monitor_rect[2] - monitor_rect[0]
                    monitor_height = monitor_rect[3] - monitor_rect[1]
                    
                    # Set to fullscreen on the detected monitor
                    self.root.geometry(f"{monitor_width}x{monitor_height}+{monitor_x}+{monitor_y}")
                    
                except Exception as e:
                    print(f"Error detecting monitor: {e}")
                    # Fallback to simple method
                    screen_width = self.root.winfo_screenwidth()
                    screen_height = self.root.winfo_screenheight()
                    self.root.geometry(f"{screen_width}x{screen_height}+0+0")
            else:
                # Fallback if win32api not available
                screen_width = self.root.winfo_screenwidth()
                screen_height = self.root.winfo_screenheight()
                self.root.geometry(f"{screen_width}x{screen_height}+0+0")
    
    def toggle_window_size(self):
        """Toggle between fullscreen and small window"""
        if self.is_fullscreen:
            # Switch to small window
            self.restore_small_window()
        else:
            # Switch to fullscreen
            self.toggle_fullscreen()
    
    def _get_monitor_info(self):
        """Get current monitor dimensions and position"""
        if win32api:
            try:
                hwnd = self.root.winfo_id()
                monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
                monitor_info = win32api.GetMonitorInfo(monitor)
                monitor_rect = monitor_info['Monitor']
                return {
                    'x': monitor_rect[0],
                    'y': monitor_rect[1],
                    'width': monitor_rect[2] - monitor_rect[0],
                    'height': monitor_rect[3] - monitor_rect[1]
                }
            except Exception:
                pass
        
        # Fallback
        return {
            'x': 0,
            'y': 0,
            'width': self.root.winfo_screenwidth(),
            'height': self.root.winfo_screenheight()
        }
    
    def snap_to_top(self):
        """Snap window to top half of screen"""
        self.log_app_event("DEBUG", "Snap to top half of screen")
        if self.is_fullscreen:
            self.is_fullscreen = False
        monitor = self._get_monitor_info()
        width = monitor['width']
        height = monitor['height'] // 2
        x = monitor['x']
        y = monitor['y']
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.window_pos_manager.save_position(self.root)
    
    def snap_to_bottom(self):
        """Snap window to bottom half of screen"""
        self.log_app_event("DEBUG", "Snap to bottom half of screen")
        if self.is_fullscreen:
            self.is_fullscreen = False
        monitor = self._get_monitor_info()
        width = monitor['width']
        height = monitor['height'] // 2
        x = monitor['x']
        y = monitor['y'] + height
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.window_pos_manager.save_position(self.root)
    
    def snap_to_left(self):
        """Snap window to left half of screen"""
        self.log_app_event("DEBUG", "Snap to left half of screen")
        if self.is_fullscreen:
            self.is_fullscreen = False
        monitor = self._get_monitor_info()
        width = monitor['width'] // 2
        height = monitor['height']
        x = monitor['x']
        y = monitor['y']
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.window_pos_manager.save_position(self.root)
    
    def snap_to_right(self):
        """Snap window to right half of screen"""
        self.log_app_event("DEBUG", "Snap to right half of screen")
        if self.is_fullscreen:
            self.is_fullscreen = False
        monitor = self._get_monitor_info()
        width = monitor['width'] // 2
        height = monitor['height']
        x = monitor['x'] + width
        y = monitor['y']
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.window_pos_manager.save_position(self.root)
    
    def restore_small_window(self):
        """Restore window to a small, centered size"""
        self.log_app_event("DEBUG", "Restore to small centered window")
        if self.is_fullscreen:
            self.is_fullscreen = False
        
        # Default small window size
        small_width = 1200
        small_height = 800
        
        # Get current monitor and center the window
        monitor = self._get_monitor_info()
        x = monitor['x'] + (monitor['width'] - small_width) // 2
        y = monitor['y'] + (monitor['height'] - small_height) // 2
        
        # Ensure window stays on screen
        x = max(monitor['x'], x)
        y = max(monitor['y'], y)
        
        self.root.geometry(f"{small_width}x{small_height}+{x}+{y}")
        self.window_pos_manager.save_position(self.root)
    
    def on_window_configure(self, event):
        """Debounced save - only save after user stops moving/resizing window"""
        # Only handle main window (not child widgets)
        if event.widget != self.root:
            return
        
        # Get current position
        try:
            geometry = self.root.geometry()
            size_pos = geometry.split('+')
            width_height = size_pos[0].split('x')
            current_pos = {
                "width": int(width_height[0]),
                "height": int(width_height[1]),
                "x": int(size_pos[1]) if len(size_pos) > 1 else 100,
                "y": int(size_pos[2]) if len(size_pos) > 2 else 100
            }
            
            # Check if position actually changed
            if current_pos == self._last_saved_position:
                return  # No change, don't save
            
            # Cancel any pending save timer
            if self._position_save_timer:
                self.root.after_cancel(self._position_save_timer)
            
            # Schedule save after 1 second of no movement
            self._position_save_timer = self.root.after(1000, lambda: self._save_position_debounced(current_pos))
            
        except Exception:
            pass  # Silently fail
    
    def _save_position_debounced(self, position):
        """Actually save the position after debounce period"""
        try:
            self.window_pos_manager.save_position(self.root)
            self._last_saved_position = position
            self._position_save_timer = None
        except Exception:
            pass
    
    def close_app(self):
        """Save window position and close the application"""
        try:
            # Save current window position before closing
            self.window_pos_manager.save_position(self.root)
        except Exception as e:
            print(f"Error saving window position on close: {e}")
        finally:
            # Close the application
            self.root.destroy()

    def update_dashboard_table_threaded(self):
        threading.Thread(target=self.update_dashboard_table).start()
        self.root.after(5000, self.update_dashboard_table_threaded)

    def load_config(self):
        """Load agent configuration from config.json file."""
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                return json.load(f)
        else:
            messagebox.showerror("Error", "config.json not found!")
            return {}
    
    def update_trading_mode_indicator(self):
        """Update the trading mode indicator on the dashboard"""
        try:
            if hasattr(self, 'trading_mode_label'):
                is_paper = self.config.get("paper_trading", True)
                trading_mode = "PAPER TRADING" if is_paper else "⚠  LIVE TRADING  ⚠"
                mode_color = "yellow" if is_paper else "red"
                self.trading_mode_label.config(text=f"Mode: {trading_mode}", foreground=mode_color)
        except Exception as e:
            print(f"Failed to update trading mode indicator: {e}")
    
    def get_agent_version(self):
        """
        Extract CDEM AGENT version number from cdem_agent.py file.
        NOTE: This is for the TRADING AGENT, NOT the app itself!
        The app version is shown in the window title bar.
        """
        try:
            with open(AGENT_SCRIPT, "r") as f:
                first_line = f.readline()
                # Parse "# Version: XX" format used in cdem_agent.py
                if "Version:" in first_line:
                    version = first_line.split("Version:")[1].strip()
                    return version
                return "Unknown"
        except Exception as e:
            return "Unknown"
    
    def get_app_version(self):
        """
        Extract APP version number from app.py (this file).
        NOTE: This is for the UI APP, NOT the trading agent!
        Used in window title bar.
        """
        try:
            # Read from app.py itself
            with open(__file__, "r") as f:
                first_line = f.readline()
                # Parse "# Version XX" format (no colon in app.py)
                if "Version" in first_line:
                    import re
                    match = re.search(r'Version\s+(\d+)', first_line)
                    if match:
                        return match.group(1)
                return "Unknown"
        except Exception as e:
            return "Unknown"

    def update_scrollbar_visibility(self, text_widget, scrollbar):
        """Auto-hide scrollbar when not needed (all content visible)."""
        try:
            # Check if scrolling is needed
            yview = text_widget.yview()
            if yview[0] == 0.0 and yview[1] == 1.0:
                scrollbar.pack_forget()  # Hide if all content visible
            else:
                scrollbar.pack(side='right', fill='y')  # Show if needed
        except:
            pass  # Silently fail if widget not ready

    def load_preferences(self):
        """Load UI preferences from preferences.json, creating defaults if missing."""
        if os.path.exists(PREFERENCES_PATH):
            with open(PREFERENCES_PATH, "r") as f:
                return json.load(f)
        else:
            # Default preferences
            defaults = {
                "config_tab": {
                    "stock_text_height": 30, 
                    "stock_text_width": 30,
                    "save_notification_duration_ms": 5000
                },
                "dashboard_tab": {"log_font_size": 10},
                "visuals_tab": {
                    "refresh_interval": 10,
                    "sentiment_time_frame_days": 30,
                },
                "logs_tab": {"font_size": 10},
            }
            with open(PREFERENCES_PATH, "w") as f:
                json.dump(defaults, f, indent=4)
            return defaults

    def show_config_status(self, message, status="success"):
        """Show temporary status message below save button (replaces popup)"""
        if hasattr(self, 'config_save_status'):
            # Set color based on status
            if status == "success":
                color = "#00ff00"  # Bright green
                icon = "✓"
            elif status == "error":
                color = "#ff3333"  # Bright red
                icon = "✗"
            else:
                color = "#ffaa00"  # Orange for warnings
                icon = "!"
            
            # Update label on main thread
            self.config_save_status.config(text=f"{icon} {message}", fg=color)
            
            # Auto-hide after user-configurable duration (default 5 seconds)
            duration_ms = self.preferences["config_tab"].get("save_notification_duration_ms", 5000)
            
            def clear_status():
                if hasattr(self, 'config_save_status'):
                    self.config_save_status.config(text="")
            
            self.root.after(duration_ms, clear_status)
    
    def _save_config_background(self, show_popup):
        """Background thread: Save config file without blocking UI."""
        try:
            # Do file I/O in background
            with open(CONFIG_PATH, "w") as f:
                json.dump(self.config, f, indent=4)
            
            # Schedule UI updates on main thread
            self.root.after(0, lambda: self.log_app_event("SUCCESS", "Config saved successfully"))
            
            # Reload config
            reloaded_config = self.load_config()
            
            # Update on main thread
            def update_ui():
                self.config = reloaded_config
                self.log_app_event("INFO", "Config reloaded from file")
                self.update_trading_mode_indicator()
                
                # NOTE: Don't call update_stock_names() here - it makes Grok API calls
                # which would freeze the UI. It's already called by save_current_config()
                # after this background save completes.
                
                if show_popup:
                    self.show_config_status("Config saved! Agent will reload automatically.", "success")
            
            self.root.after(0, update_ui)
            
        except Exception as e:
            # Schedule error handling on main thread
            def show_error():
                self.log_app_event("ERROR", f"Failed to save config: {str(e)}")
                if show_popup:
                    self.show_config_status(f"Failed to save: {str(e)}", "error")
                else:
                    print(f"Error saving config: {e}")
            
            self.root.after(0, show_error)
    
    def save_config(self, show_popup=True):
        """Save current configuration to config.json and reload (non-blocking)."""
        # Show "Saving..." status immediately (no freeze)
        if show_popup and hasattr(self, 'config_save_status'):
            self.config_save_status.config(text="⏳ Saving configuration...", fg="#00aaff")
        
        # Do actual save in background thread
        import threading
        threading.Thread(target=self._save_config_background, args=(show_popup,), daemon=True).start()

    def save_preferences(self):
        self.log_app_event("INFO", "Saving UI preferences...")
        try:
            with open(PREFERENCES_PATH, "w") as f:
                json.dump(self.preferences, f, indent=4)
            self.log_app_event("SUCCESS", "UI preferences saved successfully")
            self.log_app_event("SUCCESS", "Preferences saved")
            messagebox.showinfo("Success", "Preferences saved!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save preferences: {str(e)}")

    def setup_config_tab(self):
        """Setup configuration tab with trading parameters and stock universe editor."""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Config")

        # NOTE: Dark.TEntry, Dark.TSpinbox, Dark.TCombobox styles are now configured 
        # globally in __init__ to properly override 'alt' theme defaults

        # Top bar frame
        top_frame = tk.Frame(tab, bg="black")
        top_frame.grid(row=0, column=0, columnspan=2, sticky="ew")
        top_frame.columnconfigure(1, weight=1)  # Center column expands
        
        # Settings button (right)
        settings_button = tk.Button(
            top_frame, text="⚙", bg="gray20", fg="white", command=lambda: self.open_settings("config_tab")
        )
        settings_button.grid(row=0, column=2, sticky="e", padx=5, pady=5)

        # Left frame for settings
        left_frame = tk.Frame(tab, bg="black")
        left_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        # Right frame for stock universe
        right_frame = ttk.Frame(tab)
        right_frame.grid(row=1, column=1, sticky="nsew", padx=10, pady=10)
        tab.columnconfigure(1, weight=1)

        # ============ SYSTEM CONTROL ============
        system_frame = tk.LabelFrame(left_frame, text="⚡ System Control", 
                                      bg="black", fg="cyan", font=("Arial", 10, "bold"),
                                      bd=2, relief="groove", padx=10, pady=5)
        system_frame.pack(fill="x", pady=(0, 8))
        
        row = 0
        # Master On/Off
        tk.Label(system_frame, text="Master On:", bg="black", fg="white").grid(row=row, column=0, sticky="w", pady=2)
        self.master_on_var = tk.BooleanVar(value=self.config.get("master_on", True))
        ttk.Checkbutton(system_frame, variable=self.master_on_var).grid(row=row, column=1, sticky="w")
        info = ttk.Label(system_frame, text="?", foreground="cyan")
        info.grid(row=row, column=2, sticky="w", padx=5)
        ToolTip(info, self.descriptions["master_on"])
        row += 1

        # Paper Trading Mode
        tk.Label(system_frame, text="Paper Trading:", bg="black", fg="white").grid(row=row, column=0, sticky="w", pady=2)
        self.paper_trading_var = tk.BooleanVar(value=self.config.get("paper_trading", True))
        ttk.Checkbutton(system_frame, variable=self.paper_trading_var).grid(row=row, column=1, sticky="w")
        info = ttk.Label(system_frame, text="?", foreground="cyan")
        info.grid(row=row, column=2, sticky="w", padx=5)
        ToolTip(info, self.descriptions["paper_trading"])
        row += 1

        # Test Mode
        tk.Label(system_frame, text="Test Mode:", bg="black", fg="white").grid(row=row, column=0, sticky="w", pady=2)
        self.test_mode_var = tk.BooleanVar(value=self.config.get("test_mode", True))
        ttk.Checkbutton(system_frame, variable=self.test_mode_var).grid(row=row, column=1, sticky="w")
        info = ttk.Label(system_frame, text="?", foreground="cyan")
        info.grid(row=row, column=2, sticky="w", padx=5)
        ToolTip(info, self.descriptions["test_mode"])
        row += 1

        # Check Interval
        tk.Label(system_frame, text="Check Interval (min):", bg="black", fg="white").grid(row=row, column=0, sticky="w", pady=2)
        self.interval_var = tk.IntVar(value=self.config.get("check_interval_minutes", 60))
        ttk.Spinbox(system_frame, from_=1, to=1440, textvariable=self.interval_var, width=10, style='Dark.TSpinbox').grid(row=row, column=1, sticky="w")
        info = ttk.Label(system_frame, text="?", foreground="cyan")
        info.grid(row=row, column=2, sticky="w", padx=5)
        ToolTip(info, self.descriptions["check_interval_minutes"])

        # ============ RISK MANAGEMENT ============
        risk_frame = tk.LabelFrame(left_frame, text="⚠ Risk Management", 
                                    bg="black", fg="yellow", font=("Arial", 10, "bold"),
                                    bd=2, relief="groove", padx=10, pady=5)
        risk_frame.pack(fill="x", pady=(0, 8))
        
        row = 0
        # Risk per Trade
        tk.Label(risk_frame, text="Risk per Trade (%):", bg="black", fg="white").grid(row=row, column=0, sticky="w", pady=2)
        self.risk_var = tk.DoubleVar(value=self.config.get("risk_per_trade", 0.015) * 100)
        ttk.Entry(risk_frame, textvariable=self.risk_var, width=10, style='Dark.TEntry').grid(row=row, column=1, sticky="w")
        info = ttk.Label(risk_frame, text="?", foreground="cyan")
        info.grid(row=row, column=2, sticky="w", padx=5)
        ToolTip(info, self.descriptions["risk_per_trade"])
        row += 1

        # Max Exposure
        tk.Label(risk_frame, text="Max Exposure (%):", bg="black", fg="white").grid(row=row, column=0, sticky="w", pady=2)
        self.exposure_var = tk.DoubleVar(value=self.config.get("max_exposure", 0.45) * 100)
        ttk.Entry(risk_frame, textvariable=self.exposure_var, width=10, style='Dark.TEntry').grid(row=row, column=1, sticky="w")
        info = ttk.Label(risk_frame, text="?", foreground="cyan")
        info.grid(row=row, column=2, sticky="w", padx=5)
        ToolTip(info, self.descriptions["max_exposure"])
        row += 1

        # Stop Loss Pct
        tk.Label(risk_frame, text="Stop Loss (%):", bg="black", fg="white").grid(row=row, column=0, sticky="w", pady=2)
        self.stop_loss_var = tk.DoubleVar(value=self.config.get("stop_loss_pct", 0.05) * 100)
        ttk.Entry(risk_frame, textvariable=self.stop_loss_var, width=10, style='Dark.TEntry').grid(row=row, column=1, sticky="w")
        info = ttk.Label(risk_frame, text="?", foreground="cyan")
        info.grid(row=row, column=2, sticky="w", padx=5)
        ToolTip(info, self.descriptions["stop_loss_pct"])

        # ============ EXIT STRATEGY ============
        exit_frame = tk.LabelFrame(left_frame, text="🎯 Exit Strategy", 
                                    bg="black", fg="orange", font=("Arial", 10, "bold"),
                                    bd=2, relief="groove", padx=10, pady=5)
        exit_frame.pack(fill="x", pady=(0, 8))
        
        row = 0
        # Trailing Trigger
        tk.Label(exit_frame, text="Trailing Trigger (%):", bg="black", fg="white").grid(row=row, column=0, sticky="w", pady=2)
        self.trailing_trigger_var = tk.DoubleVar(value=self.config.get("trailing_trigger", 0.1) * 100)
        ttk.Entry(exit_frame, textvariable=self.trailing_trigger_var, width=10, style='Dark.TEntry').grid(row=row, column=1, sticky="w")
        info = ttk.Label(exit_frame, text="?", foreground="cyan")
        info.grid(row=row, column=2, sticky="w", padx=5)
        ToolTip(info, self.descriptions["trailing_trigger"])
        row += 1

        # Trailing Pct
        tk.Label(exit_frame, text="Trailing Stop (%):", bg="black", fg="white").grid(row=row, column=0, sticky="w", pady=2)
        self.trailing_pct_var = tk.DoubleVar(value=self.config.get("trailing_pct", 0.05) * 100)
        ttk.Entry(exit_frame, textvariable=self.trailing_pct_var, width=10, style='Dark.TEntry').grid(row=row, column=1, sticky="w")
        info = ttk.Label(exit_frame, text="?", foreground="cyan")
        info.grid(row=row, column=2, sticky="w", padx=5)
        ToolTip(info, self.descriptions["trailing_pct"])

        # ============ IDLE CAPITAL MANAGEMENT ============
        idle_frame = tk.LabelFrame(left_frame, text="💰 Idle Capital Management", 
                                    bg="black", fg="lightgreen", font=("Arial", 10, "bold"),
                                    bd=2, relief="groove", padx=10, pady=5)
        idle_frame.pack(fill="x", pady=(0, 8))
        
        row = 0
        # Idle Capital On/Off
        tk.Label(idle_frame, text="Auto-Invest Idle Cash:", bg="black", fg="white").grid(row=row, column=0, sticky="w", pady=2)
        self.sp_on_var = tk.BooleanVar(value=self.config.get("sp_on", True))
        ttk.Checkbutton(idle_frame, variable=self.sp_on_var).grid(row=row, column=1, sticky="w")
        info = ttk.Label(idle_frame, text="?", foreground="cyan")
        info.grid(row=row, column=2, sticky="w", padx=5)
        ToolTip(info, "Automatically invest idle cash into selected market index when not actively trading")
        row += 1
        
        # Idle Capital Target Selection
        tk.Label(idle_frame, text="Investment Target:", bg="black", fg="white").grid(row=row, column=0, sticky="w", pady=2)
        self.idle_target_var = tk.StringVar(value=self.config.get("idle_capital_target", "SPY"))
        
        self.idle_dropdown = ttk.Combobox(
            idle_frame, 
            textvariable=self.idle_target_var, 
            values=["SPY", "QQQ", "DIA", "IWM", "VOO", "VTI"],  # Removed "None" - use checkbox instead
            state="readonly",
            width=12,
            style='Dark.TCombobox'
        )
        self.idle_dropdown.grid(row=row, column=1, sticky="w")
        
        # Enable/disable dropdown based on checkbox state
        def toggle_dropdown(*args):
            if self.sp_on_var.get():
                self.idle_dropdown.config(state="readonly")
            else:
                self.idle_dropdown.config(state="disabled")
        
        self.sp_on_var.trace_add("write", toggle_dropdown)
        toggle_dropdown()  # Set initial state
        
        info = ttk.Label(idle_frame, text="?", foreground="cyan")
        info.grid(row=row, column=2, sticky="w", padx=5)
        ToolTip(info, "SPY=S&P 500 (broad market), QQQ=Nasdaq-100 (tech-heavy/Mag 7), DIA=Dow 30, IWM=Russell 2000 (small cap), VOO=Vanguard S&P 500, VTI=Total US Market. Toggle the checkbox above to enable/disable.")

        # ============ OPTIONS TRADING ============
        options_frame = tk.LabelFrame(left_frame, text="📈 Options Trading", 
                                       bg="black", fg="magenta", font=("Arial", 10, "bold"),
                                       bd=2, relief="groove", padx=10, pady=5)
        options_frame.pack(fill="x", pady=(0, 8))
        
        row = 0
        # Use Options
        tk.Label(options_frame, text="Enable Options:", bg="black", fg="white").grid(row=row, column=0, sticky="w", pady=2)
        self.use_options_var = tk.BooleanVar(value=self.config.get("use_options", False))
        ttk.Checkbutton(options_frame, variable=self.use_options_var).grid(row=row, column=1, sticky="w")
        info = ttk.Label(options_frame, text="?", foreground="cyan")
        info.grid(row=row, column=2, sticky="w", padx=5)
        ToolTip(info, self.descriptions["use_options"])
        row += 1

        # Option Exp Weeks
        tk.Label(options_frame, text="Expiration (weeks):", bg="black", fg="white").grid(row=row, column=0, sticky="w", pady=2)
        self.option_exp_var = tk.IntVar(value=self.config.get("option_exp_weeks", 1))
        ttk.Spinbox(options_frame, from_=1, to=52, textvariable=self.option_exp_var, width=10, style='Dark.TSpinbox').grid(row=row, column=1, sticky="w")
        info = ttk.Label(options_frame, text="?", foreground="cyan")
        info.grid(row=row, column=2, sticky="w", padx=5)
        ToolTip(info, self.descriptions["option_exp_weeks"])
        row += 1

        # Option Leverage
        tk.Label(options_frame, text="Leverage (%):", bg="black", fg="white").grid(row=row, column=0, sticky="w", pady=2)
        self.option_leverage_var = tk.DoubleVar(value=self.config.get("option_leverage", 0.5) * 100)
        ttk.Entry(options_frame, textvariable=self.option_leverage_var, width=10, style='Dark.TEntry').grid(row=row, column=1, sticky="w")
        info = ttk.Label(options_frame, text="?", foreground="cyan")
        info.grid(row=row, column=2, sticky="w", padx=5)
        ToolTip(info, self.descriptions["option_leverage"])

        # ============ SAVE BUTTON ============
        save_button = tk.Button(
            left_frame, text="Save Config", bg="gray20", fg="white", 
            command=self.save_current_config
        )
        save_button.pack(pady=10, fill="x", padx=5)
        
        # Status label for save feedback (replaces popup)
        self.config_save_status = tk.Label(
            left_frame,
            text="",
            bg="black",
            fg="white",
            font=("Arial", 9),
            pady=5
        )
        self.config_save_status.pack(fill="x", padx=5)

        # Stock Universe
        ttk.Label(right_frame, text="Stock Universe:").grid(row=0, column=0, sticky="w")
        stock_frame = ttk.Frame(right_frame)
        stock_frame.grid(row=1, column=0, sticky="nsew")
        right_frame.rowconfigure(1, weight=1)
        stock_frame.columnconfigure(0, weight=1)
        stock_frame.columnconfigure(1, weight=1)

        # Tickers text (using tk.Text + ttk.Scrollbar for consistent theming)
        tickers_container = tk.Frame(stock_frame, bg="black")
        tickers_container.grid(row=0, column=0, sticky="nsew")
        
        self.stock_text = tk.Text(
            tickers_container,
            height=self.preferences["config_tab"].get("stock_text_height", 30),
            width=15,
            bg="black",
            fg="cyan",
            insertbackground="white",
            highlightbackground="black",
        )
        self.tickers_scrollbar = ttk.Scrollbar(tickers_container, command=self.stock_text.yview)
        self.stock_text.config(yscrollcommand=self.tickers_scrollbar.set)
        
        self.stock_text.pack(side="left", fill="both", expand=True)
        self.tickers_scrollbar.pack(side="right", fill="y")
        
        # Auto-hide scrollbar when not needed
        self.stock_text.bind("<Configure>", lambda e: self.update_scrollbar_visibility(self.stock_text, self.tickers_scrollbar))
        self.update_scrollbar_visibility(self.stock_text, self.tickers_scrollbar)
        
        self.stock_text.tag_config("cyan", foreground="cyan")
        tickers_list = self.config.get("stock_universe", [])
        for ticker in tickers_list:
            self.stock_text.insert(tk.END, ticker + "\n", "cyan")
        
        stock_frame.rowconfigure(0, weight=1)

        # Names text (read-only, using tk.Text + ttk.Scrollbar for consistent theming)
        names_container = tk.Frame(stock_frame, bg="black")
        names_container.grid(row=0, column=1, sticky="nsew")
        
        self.name_text = tk.Text(
            names_container,
            height=self.preferences["config_tab"].get("stock_text_height", 30),
            width=30,
            bg="black",
            fg="white",
            insertbackground="white",
            highlightbackground="black",
            state="disabled",
        )
        self.names_scrollbar = ttk.Scrollbar(names_container, command=self.name_text.yview)
        self.name_text.config(yscrollcommand=self.names_scrollbar.set)
        
        self.name_text.pack(side="left", fill="both", expand=True)
        self.names_scrollbar.pack(side="right", fill="y")
        
        # Auto-hide scrollbar when not needed
        self.name_text.bind("<Configure>", lambda e: self.update_scrollbar_visibility(self.name_text, self.names_scrollbar))
        self.update_scrollbar_visibility(self.name_text, self.names_scrollbar)

        # Fetch names on load
        self.update_stock_names()

    def get_stock_color_from_grok(self, ticker, company_name):
        """Request a brand color for a stock from Grok"""
        try:
            # Reuse existing model instance (optimization)
            if self.grok_model is None:
                from src.models.model_factory import ModelFactory
                self.grok_model = ModelFactory().get_model("xai")
            
            grok_model = self.grok_model
            
            prompt = f"""Given the stock ticker {ticker} ({company_name}), suggest a single hex color code that best represents this company's brand identity. The color should:
1. Be recognizable as the company's brand color
2. Have good contrast on a black background
3. Be bright enough to be clearly visible

Respond with ONLY the hex color code (e.g., #FF6B35). No explanations, just the color code."""
            
            system_prompt = "You are a color specialist. Return only hex color codes in the format #RRGGBB."
            
            response = grok_model.generate_response(
                system_prompt=system_prompt,
                user_content=prompt,
                temperature=0.3,
                max_tokens=50
            )
            
            color = response.content.strip()
            
            # Validate hex color format - be more flexible with parsing
            if "#" in color:
                # Extract just the hex color if there's extra text
                import re
                match = re.search(r'#[0-9A-Fa-f]{6}', color)
                if match:
                    return match.group(0)
            
            return "#FFFFFF"  # Fallback to white
            
        except Exception as e:
            print(f"❌ Error getting color from Grok for {ticker}: {e}")
            return "#FFFFFF"  # Fallback to white
    
    def update_stock_names(self):
        self.name_text.config(state="normal")
        self.name_text.delete("1.0", tk.END)
        
        # Remove all existing tags
        for tag in self.name_text.tag_names():
            self.name_text.tag_delete(tag)
        
        tickers = [line.strip() for line in self.stock_text.get("1.0", tk.END).splitlines() if line.strip()]
        stock_colors = self.config.get("stock_colors", {})
        stock_names = self.config.get("stock_names", {})  # Cache for company names
        
        config_updated = False
        new_tickers = [t for t in tickers if t not in stock_colors or t not in stock_names]
        
        # Only log if there are new tickers to process
        if new_tickers:
            print(f"📊 Fetching data for {len(new_tickers)} new ticker(s): {', '.join(new_tickers)}")
            self.log_app_event("INFO", f"Fetching data for {len(new_tickers)} new ticker(s): {', '.join(new_tickers)}")
        
        for i, ticker in enumerate(tickers):
            # Get cached name or fetch from Finnhub
            if ticker in stock_names:
                name = stock_names[ticker]
            else:
                try:
                    profile = self.finnhub_client.company_profile2(symbol=ticker)
                    name = profile.get("name", "N/A")
                    stock_names[ticker] = name  # Cache the name
                    config_updated = True
                    print(f"   📝 Name cached for {ticker}: {name}")
                except:
                    name = "N/A"
                    stock_names[ticker] = name  # Cache "N/A" to avoid repeated failed lookups
                    config_updated = True
            
            # Get or request color for this ticker
            if ticker not in stock_colors:
                color = self.get_stock_color_from_grok(ticker, name)
                stock_colors[ticker] = color
                config_updated = True
                print(f"   🎨 Color {color} saved for {ticker}")
            else:
                color = stock_colors[ticker]
            
            # Create a tag for this color
            tag_name = f"color_{ticker}"
            self.name_text.tag_configure(tag_name, foreground=color)
            
            # Insert the name with the colored tag
            if i > 0:
                self.name_text.insert(tk.END, "\n")
            self.name_text.insert(tk.END, name, tag_name)
        
        # Save to config if any names or colors were updated
        if config_updated:
            self.config["stock_colors"] = stock_colors
            self.config["stock_names"] = stock_names
            self.save_config(show_popup=False)  # Silent save for automatic updates
        
        self.name_text.config(state="disabled")

    def save_current_config(self):
        self.config["master_on"] = self.master_on_var.get()
        self.config["sp_on"] = self.sp_on_var.get()
        self.config["idle_capital_target"] = self.idle_target_var.get()
        self.config["paper_trading"] = self.paper_trading_var.get()
        self.config["check_interval_minutes"] = self.interval_var.get()
        self.config["risk_per_trade"] = self.risk_var.get() / 100  # Convert % to decimal
        self.config["max_exposure"] = self.exposure_var.get() / 100  # Convert % to decimal
        self.config["stop_loss_pct"] = self.stop_loss_var.get() / 100  # Convert % to decimal
        self.config["trailing_trigger"] = self.trailing_trigger_var.get() / 100  # Convert % to decimal
        self.config["trailing_pct"] = self.trailing_pct_var.get() / 100  # Convert % to decimal
        self.config["use_options"] = self.use_options_var.get()
        self.config["option_exp_weeks"] = self.option_exp_var.get()
        self.config["option_leverage"] = self.option_leverage_var.get() / 100  # Convert % to decimal
        self.config["test_mode"] = self.test_mode_var.get()
        self.config["stock_universe"] = [
            line.strip() for line in self.stock_text.get("1.0", tk.END).splitlines() if line.strip()
        ]
        # Preserve cached stock data if it exists
        if "stock_colors" not in self.config:
            self.config["stock_colors"] = {}
        if "stock_names" not in self.config:
            self.config["stock_names"] = {}
        self.save_config()
        # Update stock names (may take time if fetching new ticker data from Grok)
        self.update_stock_names()

    def format_settings_title(self, tab_name):
        """Format tab name into a nice settings window title"""
        title_map = {
            "config_tab": "Config Tab Display Settings",
            "dashboard_tab": "Dashboard Tab Display Settings",
            "visuals_tab": "Visuals Tab Display Settings",
            "logs_tab": "Terminal Logs Tab Display Settings",
            "grok_logs_tab": "Grok Logs Tab Display Settings",
            "app_logs_tab": "App Logs Tab Display Settings",
        }
        return title_map.get(tab_name, f"{tab_name.replace('_', ' ').title()} Settings")
    
    def open_settings(self, tab_name):
        self.log_app_event("DEBUG", f"Opening settings for: {tab_name}")
        settings_win = tk.Toplevel(self.root)
        nice_title = self.format_settings_title(tab_name)
        settings_win.title(nice_title)
        settings_win.configure(bg="#1a1a1a", highlightbackground="white", highlightthickness=2)
        settings_win.overrideredirect(True)

        # Custom title bar
        title_bar_frame = tk.Frame(settings_win, height=30, bg="#1a1a1a", relief="raised")
        title_bar_frame.grid(row=0, column=0, columnspan=3, sticky="ew")
        settings_win.columnconfigure(0, weight=1)
        settings_win.columnconfigure(1, weight=0)
        settings_win.columnconfigure(2, weight=0)
        
        title_bar_frame.columnconfigure(0, weight=1)
        title_bar_frame.columnconfigure(1, weight=0)

        title_label = tk.Label(title_bar_frame, text=nice_title, 
                              foreground="white", bg="#1a1a1a", font=("Arial", 10, "bold"))
        title_label.grid(row=0, column=0, sticky="w", padx=10)

        close_button = tk.Button(
            title_bar_frame, text="✕", bg="gray20", fg="white",
            command=settings_win.destroy, width=2, font=("Arial", 10, "bold"),
        )
        close_button.grid(row=0, column=1, sticky="e", padx=2)

        # Drag bindings
        title_bar_frame.bind("<Button-1>", lambda e: self.start_move_settings(settings_win, e))
        title_bar_frame.bind("<B1-Motion>", lambda e: self.do_move_settings(settings_win, e))
        title_label.bind("<Button-1>", lambda e: self.start_move_settings(settings_win, e))
        title_label.bind("<B1-Motion>", lambda e: self.do_move_settings(settings_win, e))

        row = 1
        self._current_settings_win = settings_win
        
        # Helper function to add labeled setting with tooltip
        def add_setting(label_text, widget, tooltip_text, row_num):
            # Label
            label = tk.Label(settings_win, text=label_text, bg="#1a1a1a", fg="white", font=("Arial", 9))
            label.grid(row=row_num, column=0, sticky="w", padx=10, pady=3)
            
            # Widget (Entry, Checkbutton, etc.)
            widget.grid(row=row_num, column=1, padx=5, pady=3, sticky="w")
            
            # Tooltip icon
            tooltip_label = tk.Label(settings_win, text="?", fg="cyan", bg="#1a1a1a", 
                                    font=("Arial", 8, "bold"), cursor="question_arrow")
            tooltip_label.grid(row=row_num, column=2, padx=5, pady=3)
            ToolTip(tooltip_label, tooltip_text)
            
            return row_num + 1

        if tab_name == "config_tab":
            # Stock list dimensions
            height_var = tk.IntVar(value=self.preferences["config_tab"].get("stock_text_height", 20))
            height_entry = tk.Entry(settings_win, textvariable=height_var, bg="gray20", fg="white", 
                                   insertbackground="white", width=10)
            row = add_setting("Ticker List Height:", height_entry,
                            "Number of visible rows in the stock ticker list box", row)
            
            width_var = tk.IntVar(value=self.preferences["config_tab"].get("stock_text_width", 10))
            width_entry = tk.Entry(settings_win, textvariable=width_var, bg="gray20", fg="white", 
                                  insertbackground="white", width=10)
            row = add_setting("Ticker List Width:", width_entry,
                            "Character width of the stock ticker list box", row)
            
            # Font sizes
            ticker_font_var = tk.IntVar(value=self.preferences["config_tab"].get("ticker_font_size", 10))
            ticker_font_entry = tk.Entry(settings_win, textvariable=ticker_font_var, bg="gray20", 
                                        fg="white", insertbackground="white", width=10)
            row = add_setting("Ticker Font Size:", ticker_font_entry,
                            "Font size for stock tickers in the list (8-16 recommended)", row)
            
            name_font_var = tk.IntVar(value=self.preferences["config_tab"].get("name_font_size", 9))
            name_font_entry = tk.Entry(settings_win, textvariable=name_font_var, bg="gray20", 
                                      fg="white", insertbackground="white", width=10)
            row = add_setting("Company Name Font Size:", name_font_entry,
                            "Font size for company names (8-16 recommended)", row)
            
            # Auto-refresh colors
            auto_refresh_var = tk.BooleanVar(value=self.preferences["config_tab"].get("auto_refresh_colors", True))
            auto_refresh_check = tk.Checkbutton(settings_win, variable=auto_refresh_var, 
                                               bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Auto-Refresh Stock Colors:", auto_refresh_check,
                            "Automatically fetch brand colors for new tickers from Grok", row)
            
            # Show save confirmation
            confirm_save_var = tk.BooleanVar(value=self.preferences["config_tab"].get("confirm_save", True))
            confirm_save_check = tk.Checkbutton(settings_win, variable=confirm_save_var, 
                                               bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Show Save Confirmation:", confirm_save_check,
                            "Display a popup message when config is saved successfully", row)
            
            # Save notification duration
            save_duration_var = tk.IntVar(value=self.preferences["config_tab"].get("save_notification_duration_ms", 5000))
            save_duration_entry = tk.Entry(settings_win, textvariable=save_duration_var, bg="gray20",
                                          fg="white", insertbackground="white", width=10)
            row = add_setting("Save Notification Duration (ms):", save_duration_entry,
                            "How long the 'Config saved' message stays visible (1000-10000 ms, 1 second = 1000 ms)", row)

            def save():
                self.preferences["config_tab"]["stock_text_height"] = height_var.get()
                self.preferences["config_tab"]["stock_text_width"] = width_var.get()
                self.preferences["config_tab"]["ticker_font_size"] = ticker_font_var.get()
                self.preferences["config_tab"]["name_font_size"] = name_font_var.get()
                self.preferences["config_tab"]["auto_refresh_colors"] = auto_refresh_var.get()
                self.preferences["config_tab"]["confirm_save"] = confirm_save_var.get()
                self.preferences["config_tab"]["save_notification_duration_ms"] = save_duration_var.get()
                self.save_preferences()
                
                # Apply changes
                self.stock_text.config(height=height_var.get(), width=width_var.get(), 
                                      font=("Courier", ticker_font_var.get()))
                self.name_text.config(height=height_var.get(), 
                                     font=("Courier", name_font_var.get()))
                settings_win.destroy()

        elif tab_name == "dashboard_tab":
            # Log font size
            log_font_var = tk.IntVar(value=self.preferences["dashboard_tab"].get("log_font_size", 9))
            log_font_entry = tk.Entry(settings_win, textvariable=log_font_var, bg="gray20", 
                                     fg="white", insertbackground="white", width=10)
            row = add_setting("Log Font Size:", log_font_entry,
                            "Font size for the live agent log output (8-14 recommended)", row)
            
            # Table font size
            table_font_var = tk.IntVar(value=self.preferences["dashboard_tab"].get("table_font_size", 10))
            table_font_entry = tk.Entry(settings_win, textvariable=table_font_var, bg="gray20", 
                                       fg="white", insertbackground="white", width=10)
            row = add_setting("Table Font Size:", table_font_entry,
                            "Font size for the earnings dashboard table (8-12 recommended)", row)
            
            # Auto-scroll
            auto_scroll_var = tk.BooleanVar(value=self.preferences["dashboard_tab"].get("auto_scroll", True))
            auto_scroll_check = tk.Checkbutton(settings_win, variable=auto_scroll_var, 
                                              bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Auto-Scroll Logs:", auto_scroll_check,
                            "Automatically scroll to bottom when new log entries appear", row)
            
            # Show past earnings
            show_past_var = tk.BooleanVar(value=self.preferences["dashboard_tab"].get("show_past_earnings", True))
            show_past_check = tk.Checkbutton(settings_win, variable=show_past_var, 
                                            bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Show Past Earnings:", show_past_check,
                            "Display past earnings dates in red on the dashboard table", row)
            
            # Max log lines
            max_lines_var = tk.IntVar(value=self.preferences["dashboard_tab"].get("max_log_lines", 1000))
            max_lines_entry = tk.Entry(settings_win, textvariable=max_lines_var, bg="gray20", 
                                      fg="white", insertbackground="white", width=10)
            row = add_setting("Max Log Lines:", max_lines_entry,
                            "Maximum number of log lines to keep in memory (500-5000)", row)
            
            # Refresh rate
            refresh_rate_var = tk.IntVar(value=self.preferences["dashboard_tab"].get("refresh_rate_ms", 500))
            refresh_rate_entry = tk.Entry(settings_win, textvariable=refresh_rate_var, bg="gray20", 
                                         fg="white", insertbackground="white", width=10)
            row = add_setting("Refresh Rate (ms):", refresh_rate_entry,
                            "How often to check for dashboard updates in milliseconds (100-2000)", row)

            def save():
                self.preferences["dashboard_tab"]["log_font_size"] = log_font_var.get()
                self.preferences["dashboard_tab"]["table_font_size"] = table_font_var.get()
                self.preferences["dashboard_tab"]["auto_scroll"] = auto_scroll_var.get()
                self.preferences["dashboard_tab"]["show_past_earnings"] = show_past_var.get()
                self.preferences["dashboard_tab"]["max_log_lines"] = max_lines_var.get()
                self.preferences["dashboard_tab"]["refresh_rate_ms"] = refresh_rate_var.get()
                self.save_preferences()
                
                # Apply changes
                self.dashboard_logs_text.config(font=("Courier", log_font_var.get()))
                self.stock_table_text.config(font=("Courier", table_font_var.get()))
                settings_win.destroy()

        elif tab_name == "visuals_tab":
            # Chart refresh interval
            refresh_var = tk.IntVar(value=self.preferences["visuals_tab"].get("refresh_interval", 10))
            refresh_entry = tk.Entry(settings_win, textvariable=refresh_var, bg="gray20", 
                                    fg="white", insertbackground="white", width=10)
            row = add_setting("Chart Refresh (seconds):", refresh_entry,
                            "How often to update charts and portfolio data (5-60 seconds)", row)
            
            # Sentiment timeframe
            timeframe_var = tk.IntVar(value=self.preferences["visuals_tab"].get("sentiment_time_frame_days", 30))
            timeframe_entry = tk.Entry(settings_win, textvariable=timeframe_var, bg="gray20", 
                                      fg="white", insertbackground="white", width=10)
            row = add_setting("Sentiment History (days):", timeframe_entry,
                            "Number of days to display on the sentiment trend chart (7-90)", row)
            
            # Chart line thickness
            line_width_var = tk.IntVar(value=self.preferences["visuals_tab"].get("chart_line_width", 2))
            line_width_entry = tk.Entry(settings_win, textvariable=line_width_var, bg="gray20", 
                                       fg="white", insertbackground="white", width=10)
            row = add_setting("Chart Line Thickness:", line_width_entry,
                            "Thickness of lines on charts in pixels (1-5)", row)
            
            # Show sentiment chart
            show_sentiment_var = tk.BooleanVar(value=self.preferences["visuals_tab"].get("show_sentiment_chart", True))
            show_sentiment_check = tk.Checkbutton(settings_win, variable=show_sentiment_var, 
                                                 bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Show Sentiment Chart:", show_sentiment_check,
                            "Display the sentiment trend chart in the visuals tab", row)
            
            # Show exposure chart
            show_exposure_var = tk.BooleanVar(value=self.preferences["visuals_tab"].get("show_exposure_chart", True))
            show_exposure_check = tk.Checkbutton(settings_win, variable=show_exposure_var, 
                                                bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Show Exposure Chart:", show_exposure_check,
                            "Display the portfolio exposure pie chart in the visuals tab", row)
            
            # Max trade history rows
            max_trades_var = tk.IntVar(value=self.preferences["visuals_tab"].get("max_trade_rows", 50))
            max_trades_entry = tk.Entry(settings_win, textvariable=max_trades_var, bg="gray20", 
                                       fg="white", insertbackground="white", width=10)
            row = add_setting("Max Trade History Rows:", max_trades_entry,
                            "Maximum number of trades to display in the history table (10-200)", row)
            
            # Show closed trades only
            closed_only_var = tk.BooleanVar(value=self.preferences["visuals_tab"].get("show_closed_only", False))
            closed_only_check = tk.Checkbutton(settings_win, variable=closed_only_var, 
                                              bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Show Closed Trades Only:", closed_only_check,
                            "Hide open positions and only show completed trades", row)

            def save():
                self.preferences["visuals_tab"]["refresh_interval"] = refresh_var.get()
                self.preferences["visuals_tab"]["sentiment_time_frame_days"] = timeframe_var.get()
                self.preferences["visuals_tab"]["chart_line_width"] = line_width_var.get()
                self.preferences["visuals_tab"]["show_sentiment_chart"] = show_sentiment_var.get()
                self.preferences["visuals_tab"]["show_exposure_chart"] = show_exposure_var.get()
                self.preferences["visuals_tab"]["max_trade_rows"] = max_trades_var.get()
                self.preferences["visuals_tab"]["show_closed_only"] = closed_only_var.get()
                self.save_preferences()
                self.refresh_visuals()
                settings_win.destroy()

        elif tab_name == "logs_tab":
            # Font size
            font_var = tk.IntVar(value=self.preferences["logs_tab"].get("font_size", 9))
            font_entry = tk.Entry(settings_win, textvariable=font_var, bg="gray20", 
                                 fg="white", insertbackground="white", width=10)
            row = add_setting("Font Size:", font_entry,
                            "Font size for terminal logs (8-14 recommended)", row)
            
            # Grok logs font size
            grok_font_var = tk.IntVar(value=self.preferences["logs_tab"].get("grok_font_size", 9))
            grok_font_entry = tk.Entry(settings_win, textvariable=grok_font_var, bg="gray20", 
                                      fg="white", insertbackground="white", width=10)
            row = add_setting("Grok Logs Font Size:", grok_font_entry,
                            "Font size for Grok API logs (8-14 recommended)", row)
            
            # Auto-scroll
            auto_scroll_var = tk.BooleanVar(value=self.preferences["logs_tab"].get("auto_scroll", True))
            auto_scroll_check = tk.Checkbutton(settings_win, variable=auto_scroll_var, 
                                              bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Auto-Scroll Logs:", auto_scroll_check,
                            "Automatically scroll to bottom when new log entries appear", row)
            
            # Max lines
            max_lines_var = tk.IntVar(value=self.preferences["logs_tab"].get("max_lines", 2000))
            max_lines_entry = tk.Entry(settings_win, textvariable=max_lines_var, bg="gray20", 
                                      fg="white", insertbackground="white", width=10)
            row = add_setting("Max Log Lines:", max_lines_entry,
                            "Maximum number of log lines to keep in memory (1000-10000)", row)
            
            # Show timestamps
            show_timestamps_var = tk.BooleanVar(value=self.preferences["logs_tab"].get("show_timestamps", True))
            show_timestamps_check = tk.Checkbutton(settings_win, variable=show_timestamps_var, 
                                                  bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Show Timestamps:", show_timestamps_check,
                            "Display timestamps for each log entry (Grok logs)", row)
            
            # Wrap long lines
            wrap_lines_var = tk.BooleanVar(value=self.preferences["logs_tab"].get("wrap_lines", False))
            wrap_lines_check = tk.Checkbutton(settings_win, variable=wrap_lines_var, 
                                             bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Wrap Long Lines:", wrap_lines_check,
                            "Wrap long lines instead of showing horizontal scrollbar", row)

            def save():
                self.preferences["logs_tab"]["font_size"] = font_var.get()
                self.preferences["logs_tab"]["grok_font_size"] = grok_font_var.get()
                self.preferences["logs_tab"]["auto_scroll"] = auto_scroll_var.get()
                self.preferences["logs_tab"]["max_lines"] = max_lines_var.get()
                self.preferences["logs_tab"]["show_timestamps"] = show_timestamps_var.get()
                self.preferences["logs_tab"]["wrap_lines"] = wrap_lines_var.get()
                self.save_preferences()
                
                # Apply changes
                self.logs_text.config(font=("Courier", font_var.get()))
                self.grok_logs_text.config(font=("Consolas", grok_font_var.get()))
                if wrap_lines_var.get():
                    self.logs_text.config(wrap=tk.WORD)
                    self.grok_logs_text.config(wrap=tk.WORD)
                else:
                    self.logs_text.config(wrap=tk.NONE)
                    self.grok_logs_text.config(wrap=tk.NONE)
                settings_win.destroy()

        elif tab_name == "grok_logs_tab":
            # Font size
            font_var = tk.IntVar(value=self.preferences.get("grok_logs_tab", {}).get("font_size", 10))
            font_entry = tk.Entry(settings_win, textvariable=font_var, bg="gray20", 
                                 fg="white", insertbackground="white", width=10)
            row = add_setting("Font Size:", font_entry,
                            "Font size for Grok API logs (8-14 recommended)", row)
            
            # Response text width
            text_width_var = tk.IntVar(value=self.preferences.get("grok_logs_tab", {}).get("response_text_width", 70))
            text_width_entry = tk.Entry(settings_win, textvariable=text_width_var, bg="gray20", 
                                       fg="white", insertbackground="white", width=10)
            row = add_setting("Response Text Width:", text_width_entry,
                            "Character width for wrapping Grok response text (40-120 recommended)", row)
            
            # Timestamp color
            timestamp_colors = ["magenta", "cyan", "yellow", "green", "white"]
            timestamp_color_var = tk.StringVar(value=self.preferences.get("grok_logs_tab", {}).get("timestamp_color", "magenta"))
            timestamp_color_menu = tk.OptionMenu(settings_win, timestamp_color_var, *timestamp_colors)
            timestamp_color_menu.config(bg="gray20", fg="white", highlightbackground="#1a1a1a")
            row = add_setting("Timestamp Color:", timestamp_color_menu,
                            "Color for timestamp text in Grok logs", row)
            
            # Show separators
            show_separators_var = tk.BooleanVar(value=self.preferences.get("grok_logs_tab", {}).get("show_separators", True))
            show_separators_check = tk.Checkbutton(settings_win, variable=show_separators_var, 
                                                   bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Show Separators:", show_separators_check,
                            "Display separator lines between log entries", row)
            
            # Auto-scroll
            auto_scroll_var = tk.BooleanVar(value=self.preferences.get("grok_logs_tab", {}).get("auto_scroll", True))
            auto_scroll_check = tk.Checkbutton(settings_win, variable=auto_scroll_var, 
                                              bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Auto-Scroll Logs:", auto_scroll_check,
                            "Automatically scroll to bottom when new log entries appear", row)
            
            # Max entries
            max_entries_var = tk.IntVar(value=self.preferences.get("grok_logs_tab", {}).get("max_entries", 100))
            max_entries_entry = tk.Entry(settings_win, textvariable=max_entries_var, bg="gray20", 
                                        fg="white", insertbackground="white", width=10)
            row = add_setting("Max Log Entries:", max_entries_entry,
                            "Maximum number of Grok log entries to display (20-500)", row)
            
            # Show reasoning
            show_reasoning_var = tk.BooleanVar(value=self.preferences.get("grok_logs_tab", {}).get("show_reasoning", True))
            show_reasoning_check = tk.Checkbutton(settings_win, variable=show_reasoning_var, 
                                                 bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Show Reasoning:", show_reasoning_check,
                            "Display full reasoning text from Grok responses", row)

            def save():
                if "grok_logs_tab" not in self.preferences:
                    self.preferences["grok_logs_tab"] = {}
                
                self.preferences["grok_logs_tab"]["font_size"] = font_var.get()
                self.preferences["grok_logs_tab"]["response_text_width"] = text_width_var.get()
                self.preferences["grok_logs_tab"]["timestamp_color"] = timestamp_color_var.get()
                self.preferences["grok_logs_tab"]["show_separators"] = show_separators_var.get()
                self.preferences["grok_logs_tab"]["auto_scroll"] = auto_scroll_var.get()
                self.preferences["grok_logs_tab"]["max_entries"] = max_entries_var.get()
                self.preferences["grok_logs_tab"]["show_reasoning"] = show_reasoning_var.get()
                self.save_preferences()
                
                # Apply changes
                self.grok_logs_text.config(font=("Consolas", font_var.get()))
                self.grok_logs_text.tag_config("timestamp", foreground=timestamp_color_var.get())
                self.update_grok_logs()  # Refresh logs with new settings
                settings_win.destroy()
        
        elif tab_name == "app_logs_tab":
            # Font size
            font_var = tk.IntVar(value=self.preferences.get("app_logs_tab", {}).get("font_size", 9))
            font_entry = tk.Entry(settings_win, textvariable=font_var, bg="gray20", 
                                 fg="white", insertbackground="white", width=10)
            row = add_setting("Font Size:", font_entry,
                            "Font size for app event logs (8-14 recommended)", row)
            
            # Max log lines (in UI display)
            max_lines_var = tk.IntVar(value=self.preferences.get("app_logs_tab", {}).get("max_lines", 1000))
            max_lines_entry = tk.Entry(settings_win, textvariable=max_lines_var, bg="gray20", 
                                      fg="white", insertbackground="white", width=10)
            row = add_setting("Max UI Lines:", max_lines_entry,
                            "Maximum lines to keep in UI display (file still logs all). 500-2000 recommended", row)
            
            # Auto-scroll
            auto_scroll_var = tk.BooleanVar(value=self.preferences.get("app_logs_tab", {}).get("auto_scroll", True))
            auto_scroll_check = tk.Checkbutton(settings_win, variable=auto_scroll_var, 
                                              bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Auto-Scroll:", auto_scroll_check,
                            "Automatically scroll to bottom when new events occur", row)
            
            # Show timestamps
            show_timestamps_var = tk.BooleanVar(value=self.preferences.get("app_logs_tab", {}).get("show_timestamps", True))
            show_timestamps_check = tk.Checkbutton(settings_win, variable=show_timestamps_var, 
                                                   bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Show Timestamps:", show_timestamps_check,
                            "Display timestamp for each log entry", row)
            
            # Log level filter
            log_levels = ["ALL", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"]
            log_level_var = tk.StringVar(value=self.preferences.get("app_logs_tab", {}).get("log_level_filter", "ALL"))
            log_level_menu = tk.OptionMenu(settings_win, log_level_var, *log_levels)
            log_level_menu.config(bg="gray20", fg="white", highlightbackground="#1a1a1a")
            row = add_setting("Log Level Filter:", log_level_menu,
                            "Show only logs at or above this level (ALL shows everything)", row)
            
            # Word wrap
            word_wrap_var = tk.BooleanVar(value=self.preferences.get("app_logs_tab", {}).get("word_wrap", True))
            word_wrap_check = tk.Checkbutton(settings_win, variable=word_wrap_var, 
                                            bg="#1a1a1a", fg="white", selectcolor="gray20")
            row = add_setting("Word Wrap:", word_wrap_check,
                            "Wrap long lines instead of horizontal scrolling", row)

            def save():
                if "app_logs_tab" not in self.preferences:
                    self.preferences["app_logs_tab"] = {}
                
                self.preferences["app_logs_tab"]["font_size"] = font_var.get()
                self.preferences["app_logs_tab"]["max_lines"] = max_lines_var.get()
                self.preferences["app_logs_tab"]["auto_scroll"] = auto_scroll_var.get()
                self.preferences["app_logs_tab"]["show_timestamps"] = show_timestamps_var.get()
                self.preferences["app_logs_tab"]["log_level_filter"] = log_level_var.get()
                self.preferences["app_logs_tab"]["word_wrap"] = word_wrap_var.get()
                self.save_preferences()
                
                # Apply changes
                self.app_logs_text.config(font=("Courier", font_var.get()))
                wrap_mode = tk.WORD if word_wrap_var.get() else tk.NONE
                self.app_logs_text.config(wrap=wrap_mode)
                settings_win.destroy()
                
                self.log_app_event("INFO", f"App logs settings updated: font={font_var.get()}, max_lines={max_lines_var.get()}")

        save_button = tk.Button(settings_win, text="Save", bg="gray20", fg="white", command=save)
        save_button.grid(row=row, column=0, columnspan=3, pady=10)
        
        # Center the settings window on the main window
        settings_win.update_idletasks()  # Force geometry calculation
        
        # Get main window position and size
        main_x = self.root.winfo_x()
        main_y = self.root.winfo_y()
        main_width = self.root.winfo_width()
        main_height = self.root.winfo_height()
        
        # Get settings window size
        settings_width = settings_win.winfo_width()
        settings_height = settings_win.winfo_height()
        
        # Calculate center position
        center_x = main_x + (main_width - settings_width) // 2
        center_y = main_y + (main_height - settings_height) // 2
        
        # Set position
        settings_win.geometry(f"+{center_x}+{center_y}")

    def start_move_settings(self, win, event):
        self.settings_x = event.x
        self.settings_y = event.y

    def do_move_settings(self, win, event):
        deltax = event.x - self.settings_x
        deltay = event.y - self.settings_y
        x = win.winfo_x() + deltax
        y = win.winfo_y() + deltay
        win.geometry(f"+{x}+{y}")

    def setup_dashboard_tab(self):
        """Setup dashboard tab with agent status, controls, and live logs."""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Dashboard")

        # Top bar frame with version and settings
        top_bar = tk.Frame(tab, bg="black")
        top_bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        top_bar.columnconfigure(0, weight=1)  # Left expands
        
        # IMPORTANT: This displays CDEM AGENT version (from cdem_agent.py)
        # NOT the app version (which is shown in window title bar at top)
        agent_version = self.get_agent_version()
        version_label = ttk.Label(
            top_bar, 
            text=f"CDEM Agent Version: {agent_version}", 
            foreground="cyan",
            font=("Consolas", 11, "bold")
        )
        version_label.pack(side="left", padx=5, pady=5)
        
        # Settings button (right)
        settings_button = tk.Button(
            top_bar, text="⚙", bg="gray20", fg="white", command=lambda: self.open_settings("dashboard_tab")
        )
        settings_button.pack(side="right", padx=5, pady=5)
        
        # Trading mode status indicator (spanning both columns)
        trading_mode = "PAPER TRADING" if self.config.get("paper_trading", True) else "⚠  LIVE TRADING  ⚠"
        mode_color = "yellow" if self.config.get("paper_trading", True) else "red"
        self.trading_mode_label = ttk.Label(
            tab,
            text=f"Mode: {trading_mode}",
            foreground=mode_color,
            font=("Consolas", 12, "bold")
        )
        self.trading_mode_label.grid(row=1, column=0, columnspan=2, sticky="n", pady=2)
        
        # Account Statistics Frame
        stats_container = ttk.Frame(tab)
        stats_container.grid(row=2, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 10))
        
        # Paper Account Stats
        paper_frame = tk.Frame(stats_container, bg="black", relief="raised", bd=2)
        paper_frame.pack(side="left", expand=True, fill="both", padx=(0, 5))
        
        tk.Label(paper_frame, text="📄 PAPER ACCOUNT", font=("Consolas", 10, "bold"),
                bg="black", fg="yellow").pack(pady=(5, 2))
        
        # Balance row
        balance_row = tk.Frame(paper_frame, bg="black")
        balance_row.pack()
        self.paper_balance_label = tk.Label(balance_row, text="Balance: $0.00", 
                                            font=("Consolas", 9), bg="black", fg="white")
        self.paper_balance_label.pack(side="left")
        balance_info = ttk.Label(balance_row, text="?", foreground="cyan", background="black", font=("Arial", 8))
        balance_info.pack(side="left", padx=3)
        ToolTip(balance_info, "Total account equity (cash + positions value). This is your complete account value.")
        
        # Buying Power row
        bp_row = tk.Frame(paper_frame, bg="black")
        bp_row.pack()
        self.paper_buying_power_label = tk.Label(bp_row, text="Buying Power: $0.00",
                                                 font=("Consolas", 9), bg="black", fg="white")
        self.paper_buying_power_label.pack(side="left")
        bp_info = ttk.Label(bp_row, text="?", foreground="cyan", background="black", font=("Arial", 8))
        bp_info.pack(side="left", padx=3)
        ToolTip(bp_info, "Available funds for new trades. Shows stock buying power (or options buying power if options are enabled in config). Can be higher than cash if margin is enabled.")
        
        # Open Positions row
        pos_row = tk.Frame(paper_frame, bg="black")
        pos_row.pack(pady=(0, 5))
        self.paper_positions_label = tk.Label(pos_row, text="Open Positions: 0",
                                              font=("Consolas", 9), bg="black", fg="white")
        self.paper_positions_label.pack(side="left")
        pos_info = ttk.Label(pos_row, text="?", foreground="cyan", background="black", font=("Arial", 8))
        pos_info.pack(side="left", padx=3)
        ToolTip(pos_info, "Number of currently open stock positions in this account.")
        
        # Live Account Stats
        live_frame = tk.Frame(stats_container, bg="black", relief="raised", bd=2)
        live_frame.pack(side="left", expand=True, fill="both", padx=(5, 0))
        
        tk.Label(live_frame, text="💰 LIVE ACCOUNT", font=("Consolas", 10, "bold"),
                bg="black", fg="red").pack(pady=(5, 2))
        
        # Balance row
        live_balance_row = tk.Frame(live_frame, bg="black")
        live_balance_row.pack()
        self.live_balance_label = tk.Label(live_balance_row, text="Balance: $0.00",
                                           font=("Consolas", 9), bg="black", fg="white")
        self.live_balance_label.pack(side="left")
        live_balance_info = ttk.Label(live_balance_row, text="?", foreground="cyan", background="black", font=("Arial", 8))
        live_balance_info.pack(side="left", padx=3)
        ToolTip(live_balance_info, "Total account equity (cash + positions value). This is your complete account value.")
        
        # Buying Power row
        live_bp_row = tk.Frame(live_frame, bg="black")
        live_bp_row.pack()
        self.live_buying_power_label = tk.Label(live_bp_row, text="Buying Power: $0.00",
                                                font=("Consolas", 9), bg="black", fg="white")
        self.live_buying_power_label.pack(side="left")
        live_bp_info = ttk.Label(live_bp_row, text="?", foreground="cyan", background="black", font=("Arial", 8))
        live_bp_info.pack(side="left", padx=3)
        ToolTip(live_bp_info, "Available funds for new trades. Shows stock buying power (or options buying power if options are enabled in config). Can be higher than cash if margin is enabled.")
        
        # Open Positions row
        live_pos_row = tk.Frame(live_frame, bg="black")
        live_pos_row.pack(pady=(0, 5))
        self.live_positions_label = tk.Label(live_pos_row, text="Open Positions: 0",
                                             font=("Consolas", 9), bg="black", fg="white")
        self.live_positions_label.pack(side="left")
        live_pos_info = ttk.Label(live_pos_row, text="?", foreground="cyan", background="black", font=("Arial", 8))
        live_pos_info.pack(side="left", padx=3)
        ToolTip(live_pos_info, "Number of currently open stock positions in this account.")

        # Left frame for table (narrowed width)
        left_frame = ttk.Frame(tab)
        left_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=10)
        tab.columnconfigure(0, weight=9)  # Adjusted to ~30%
        tab.rowconfigure(3, weight=1)

        # Right frame for controls and logs (widened)
        right_frame = ttk.Frame(tab)
        right_frame.grid(row=3, column=1, sticky="nsew", padx=10, pady=10)
        tab.columnconfigure(1, weight=21)  # Adjusted to ~70%

        # Allow the column to expand and fill the frame width
        right_frame.columnconfigure(0, weight=1)
        
        # Dashboard table header with info tooltip
        dashboard_header_frame = tk.Frame(left_frame, bg="black")
        dashboard_header_frame.grid(row=0, column=0, sticky="w", pady=(0, 5))
        
        dashboard_title = tk.Label(
            dashboard_header_frame, text="📊 Earnings Dashboard", font=("Consolas", 11, "bold"),
            bg="black", fg="cyan"
        )
        dashboard_title.pack(side="left")
        
        dashboard_info = ttk.Label(dashboard_header_frame, text="?", foreground="cyan", background="black", font=("Arial", 9))
        dashboard_info.pack(side="left", padx=5)
        
        dashboard_tooltip = """Dashboard Column Descriptions:

• Ticker: Stock symbol (colored by company brand)
• Earnings Date: Scheduled earnings report date
  - White: Upcoming earnings
  - Red: Past earnings (already reported)
• Trade Time: Time when trade should be executed
  - Shows when position was or will be entered
• Sentiment: AI consensus analysis (1 day before earnings)
  - Green: Good (positive outlook, likely to beat)
  - Orange: Mixed (uncertain, balanced views)
  - Red: Bad (negative outlook, concerns)
  - Gray: Pending (not yet analyzed)"""
        
        ToolTip(dashboard_info, dashboard_tooltip)
        
        # Configure left_frame grid for header and table
        left_frame.rowconfigure(0, weight=0)  # Header row
        left_frame.rowconfigure(1, weight=1)  # Table row
        left_frame.columnconfigure(0, weight=1)

        # Dashboard Table (using tk.Text + ttk.Scrollbar for consistent theming)
        table_container = tk.Frame(left_frame, bg="black")
        table_container.grid(row=1, column=0, sticky="nsew")
        
        self.stock_table_text = tk.Text(
            table_container,
            wrap=tk.NONE,
            font=("Courier", 10),
            bg="black",
            fg="white",
            insertbackground="white",
            highlightbackground="black",
        )
        self.table_scrollbar = ttk.Scrollbar(table_container, command=self.stock_table_text.yview)
        self.stock_table_text.config(yscrollcommand=self.table_scrollbar.set)
        
        self.stock_table_text.pack(side="left", fill="both", expand=True)
        self.table_scrollbar.pack(side="right", fill="y")
        
        # Auto-hide scrollbar when not needed
        self.stock_table_text.bind("<Configure>", lambda e: self.update_scrollbar_visibility(self.stock_table_text, self.table_scrollbar))
        self.update_scrollbar_visibility(self.stock_table_text, self.table_scrollbar)

        # Add tags for sentiment colors
        self.stock_table_text.tag_config("good", foreground="green")
        self.stock_table_text.tag_config("mixed", foreground="orange")
        self.stock_table_text.tag_config("bad", foreground="red")
        self.stock_table_text.tag_config("pending", foreground="gray")
        self.stock_table_text.tag_config("cyan", foreground="cyan")
        self.stock_table_text.tag_config("red_date", foreground="red")
        self.stock_table_text.tag_config("white", foreground="white")

        # Status Label
        self.status_label = ttk.Label(right_frame, text="Agent Status: Stopped", foreground="red")
        self.status_label.grid(row=0, column=0, sticky="w")

        # Button frame for start and stop buttons side by side
        button_frame = ttk.Frame(right_frame)
        button_frame.grid(row=1, column=0, sticky="ew")
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)

        # Start button with green dot
        start_subframe = ttk.Frame(button_frame)
        start_subframe.grid(row=0, column=0, sticky="ew")
        green_dot = tk.Label(start_subframe, text="•", fg="green", bg="black", font=("Arial", 20))
        green_dot.pack(side="left")
        start_button = tk.Button(
            start_subframe, text="Start Agent", bg="gray20", fg="white", command=self.start_agent_threaded
        )
        start_button.pack(fill="x", expand=True)

        # Stop button with red dot
        stop_subframe = ttk.Frame(button_frame)
        stop_subframe.grid(row=0, column=1, sticky="ew")
        red_dot = tk.Label(stop_subframe, text="•", fg="red", bg="black", font=("Arial", 20))
        red_dot.pack(side="left")
        stop_button = tk.Button(stop_subframe, text="Stop Agent", bg="gray20", fg="white", command=self.stop_agent)
        stop_button.pack(fill="x", expand=True)

        # Open Terminal Button
        terminal_button = tk.Button(
            right_frame, text="Open Terminal", bg="gray20", fg="white", command=self.open_terminal
        )
        terminal_button.grid(row=2, column=0, pady=5, sticky="ew")

        # Dashboard Logs (with ttk scrollbar like test 20)
        dashboard_logs_frame = tk.Frame(right_frame, bg="black")
        dashboard_logs_frame.grid(row=3, column=0, sticky="nsew")
        right_frame.rowconfigure(3, weight=1)
        
        self.dashboard_logs_text = tk.Text(
            dashboard_logs_frame,
            wrap=tk.WORD,
            font=("Courier", self.preferences["dashboard_tab"].get("log_font_size", 10)),
            bg="black",
            fg="white",
            insertbackground="white",
            highlightbackground="black",
        )
        # Make read-only: prevent user editing while allowing programmatic updates
        self.dashboard_logs_text.bind("<Key>", lambda e: "break")
        self.dashboard_logs_scrollbar = ttk.Scrollbar(dashboard_logs_frame, command=self.dashboard_logs_text.yview)
        self.dashboard_logs_text.config(yscrollcommand=self.dashboard_logs_scrollbar.set)
        
        self.dashboard_logs_text.pack(side='left', fill='both', expand=True)
        self.dashboard_logs_scrollbar.pack(side='right', fill='y')
        
        # Auto-hide scrollbar when not needed
        self.dashboard_logs_text.bind("<Configure>", lambda e: self.update_scrollbar_visibility(self.dashboard_logs_text, self.dashboard_logs_scrollbar))
        self.update_scrollbar_visibility(self.dashboard_logs_text, self.dashboard_logs_scrollbar)

        # Configure color tags
        ANSIParser.configure_tags(self.dashboard_logs_text)

        self.update_dashboard_table()

    def open_terminal(self):
        self.log_app_event("DEBUG", "Opening terminal with conda environment")
        subprocess.Popen('start cmd /K "conda activate cdem-agent-env"', shell=True)

    def start_agent_threaded(self):
        self.log_app_event("INFO", "Start agent button clicked")
        if not self.running:
            threading.Thread(target=self.start_agent).start()
        else:
            self.log_app_event("WARNING", "Agent already running - ignoring start request")

    def update_account_balances(self):
        """Launch threaded account balance update to avoid blocking UI."""
        threading.Thread(target=self._update_account_balances_background, daemon=True).start()
    
    def _update_account_balances_background(self):
        """Background thread: Fetch account balances without blocking UI."""
        try:
            import requests
            
            # Get API keys from environment
            paper_api_key = os.getenv("TRADIER_PAPER_API_KEY")
            paper_account_id = os.getenv("TRADIER_PAPER_ACCOUNT_ID")
            live_api_key = os.getenv("TRADIER_API_KEY")
            live_account_id = os.getenv("TRADIER_ACCOUNT_ID")
            
            self.root.after(0, lambda: self.log_app_event("DEBUG", "Starting account balance fetch"))
            
            # Fetch Paper Account data
            paper_balance_text = "Balance: N/A"
            paper_buying_power_text = "Buying Power: N/A"
            paper_positions_text = "Open Positions: N/A"
            
            if paper_api_key and paper_account_id:
                try:
                    endpoint = f"https://sandbox.tradier.com/v1/accounts/{paper_account_id}/balances"
                    self.root.after(0, lambda: self.log_app_event("DEBUG", f"📤 PAPER API Request: GET {endpoint}"))
                    
                    response = requests.get(
                        endpoint,
                        headers={"Authorization": f"Bearer {paper_api_key}", "Accept": "application/json"},
                        timeout=15
                    )
                    
                    self.root.after(0, lambda: self.log_app_event("DEBUG", f"📥 PAPER API Response: Status {response.status_code}"))
                    
                    if response.status_code == 200:
                        data = response.json()
                        balances = data.get("balances", {})
                        
                        # Log the raw balances data
                        self.root.after(0, lambda b=balances: self.log_app_event("DEBUG", f"📊 PAPER Raw Balances: {b}"))
                        
                        balance = float(balances.get("total_equity", 0))
                        account_type = balances.get("account_type", "margin")
                        
                        # Get buying power based on account type and config
                        use_options = self.config.get("use_options", False)
                        if account_type == "margin":
                            # Margin account: buying power is nested in 'margin' dict
                            margin_info = balances.get("margin", {})
                            if use_options:
                                buying_power = float(margin_info.get("option_buying_power", 0))
                                bp_type = "margin.option_buying_power"
                            else:
                                buying_power = float(margin_info.get("stock_buying_power", 0))
                                bp_type = "margin.stock_buying_power"
                        else:
                            # Cash account: use cash_available
                            cash_info = balances.get("cash", {})
                            buying_power = float(cash_info.get("cash_available", 0))
                            bp_type = "cash.cash_available"
                        
                        self.root.after(0, lambda: self.log_app_event("INFO", f"💰 PAPER: total_equity=${balance:,.2f}, {bp_type}=${buying_power:,.2f}"))
                        
                        paper_balance_text = f"Balance: ${balance:,.2f}"
                        paper_buying_power_text = f"Buying Power: ${buying_power:,.2f}"
                        
                        # Get positions count
                        pos_response = requests.get(
                            f"https://sandbox.tradier.com/v1/accounts/{paper_account_id}/positions",
                            headers={"Authorization": f"Bearer {paper_api_key}", "Accept": "application/json"},
                            timeout=15
                        )
                        if pos_response.status_code == 200:
                            pos_data = pos_response.json()
                            positions = pos_data.get("positions")
                            if positions and positions != "null":
                                if isinstance(positions, dict):
                                    position_list = positions.get("position", [])
                                    if isinstance(position_list, dict):
                                        count = 1
                                    else:
                                        count = len(position_list) if position_list else 0
                                else:
                                    count = 0
                            else:
                                count = 0
                            paper_positions_text = f"Open Positions: {count}"
                    else:
                        self.root.after(0, lambda s=response.status_code: self.log_app_event("ERROR", f"PAPER API Error: Status {s}"))
                except Exception as e:
                    self.root.after(0, lambda err=str(e): self.log_app_event("ERROR", f"PAPER API Exception: {err}"))
            else:
                self.root.after(0, lambda: self.log_app_event("WARNING", "PAPER account credentials not found in .env"))
            
            # Fetch Live Account data
            live_balance_text = "Balance: N/A"
            live_buying_power_text = "Buying Power: N/A"
            live_positions_text = "Open Positions: N/A"
            
            if live_api_key and live_account_id:
                try:
                    endpoint = f"https://api.tradier.com/v1/accounts/{live_account_id}/balances"
                    self.root.after(0, lambda: self.log_app_event("DEBUG", f"📤 LIVE API Request: GET {endpoint}"))
                    
                    response = requests.get(
                        endpoint,
                        headers={"Authorization": f"Bearer {live_api_key}", "Accept": "application/json"},
                        timeout=15
                    )
                    
                    self.root.after(0, lambda: self.log_app_event("DEBUG", f"📥 LIVE API Response: Status {response.status_code}"))
                    
                    if response.status_code == 200:
                        data = response.json()
                        balances = data.get("balances", {})
                        
                        # Log the raw balances data
                        self.root.after(0, lambda b=balances: self.log_app_event("DEBUG", f"📊 LIVE Raw Balances: {b}"))
                        
                        balance = float(balances.get("total_equity", 0))
                        account_type = balances.get("account_type", "margin")
                        
                        # Get buying power based on account type and config
                        use_options = self.config.get("use_options", False)
                        if account_type == "margin":
                            # Margin account: buying power is nested in 'margin' dict
                            margin_info = balances.get("margin", {})
                            if use_options:
                                buying_power = float(margin_info.get("option_buying_power", 0))
                                bp_type = "margin.option_buying_power"
                            else:
                                buying_power = float(margin_info.get("stock_buying_power", 0))
                                bp_type = "margin.stock_buying_power"
                        else:
                            # Cash account: use cash_available
                            cash_info = balances.get("cash", {})
                            buying_power = float(cash_info.get("cash_available", 0))
                            bp_type = "cash.cash_available"
                        
                        self.root.after(0, lambda: self.log_app_event("INFO", f"💰 LIVE: total_equity=${balance:,.2f}, {bp_type}=${buying_power:,.2f}"))
                        
                        live_balance_text = f"Balance: ${balance:,.2f}"
                        live_buying_power_text = f"Buying Power: ${buying_power:,.2f}"
                        
                        # Get positions count
                        pos_response = requests.get(
                            f"https://api.tradier.com/v1/accounts/{live_account_id}/positions",
                            headers={"Authorization": f"Bearer {live_api_key}", "Accept": "application/json"},
                            timeout=15
                        )
                        if pos_response.status_code == 200:
                            pos_data = pos_response.json()
                            positions = pos_data.get("positions")
                            if positions and positions != "null":
                                if isinstance(positions, dict):
                                    position_list = positions.get("position", [])
                                    if isinstance(position_list, dict):
                                        count = 1
                                    else:
                                        count = len(position_list) if position_list else 0
                                else:
                                    count = 0
                            else:
                                count = 0
                            live_positions_text = f"Open Positions: {count}"
                    else:
                        self.root.after(0, lambda s=response.status_code: self.log_app_event("ERROR", f"LIVE API Error: Status {s}"))
                except Exception as e:
                    self.root.after(0, lambda err=str(e): self.log_app_event("ERROR", f"LIVE API Exception: {err}"))
            else:
                self.root.after(0, lambda: self.log_app_event("WARNING", "LIVE account credentials not found in .env"))
            
            # Schedule UI updates on main thread
            self.root.after(0, lambda: self._update_account_balances_ui(
                paper_balance_text, paper_buying_power_text, paper_positions_text,
                live_balance_text, live_buying_power_text, live_positions_text
            ))
            
            self.root.after(0, lambda: self.log_app_event("DEBUG", "Account balance fetch completed"))
                    
        except Exception as e:
            error_msg = f"Critical error in account balance update: {e}"
            print(error_msg)
            self.root.after(0, lambda: self.log_app_event("ERROR", error_msg))
        
        # Schedule next update in 30 seconds
        self.root.after(30000, self.update_account_balances)
    
    def _update_account_balances_ui(self, paper_balance, paper_buying_power, paper_positions,
                                     live_balance, live_buying_power, live_positions):
        """Update account balance UI elements on main thread."""
        try:
            self.paper_balance_label.config(text=paper_balance)
            self.paper_buying_power_label.config(text=paper_buying_power)
            self.paper_positions_label.config(text=paper_positions)
            
            self.live_balance_label.config(text=live_balance)
            self.live_buying_power_label.config(text=live_buying_power)
            self.live_positions_label.config(text=live_positions)
        except Exception as e:
            print(f"Error updating account balance UI: {e}")
    
    def update_dashboard_table(self):
        """Update dashboard table with latest earnings dates and sentiment data."""
        try:
            from datetime import datetime
            
            # Build new content first to check if update is needed
            new_content_lines = []
            
            # Build header (Fixed width)
            header = f"{'Ticker':<10}{'Earnings Date':<15}{'Trade Time':<15}{'Sentiment':<10}\n"

            # Parse logs for earnings and consensus
            earnings = {}
            sentiment = {}
            trade_times = {}  # Placeholder; parse from entry logs if available

            # Parse all_logs for earnings and consensus
            for log in self.all_logs:
                # Strip ANSI color codes for parsing
                clean_log = re.sub(r'\x1b\[\d+m', '', log)
                
                # Match upcoming earnings: "Found earnings for TICKER on YYYY-MM-DD"
                earnings_match = re.search(r"Found earnings for (\w+) on (\d{4}-\d{2}-\d{2})", clean_log)
                if earnings_match:
                    ticker, date = earnings_match.groups()
                    earnings[ticker] = date
                
                # Match past earnings: "No upcoming earnings for TICKER (last: YYYY-MM-DD)"
                past_earnings_match = re.search(r"No upcoming earnings for (\w+) \(last: (\d{4}-\d{2}-\d{2})\)", clean_log)
                if past_earnings_match:
                    ticker, date = past_earnings_match.groups()
                    # Only use past date if no upcoming date exists
                    if ticker not in earnings:
                        earnings[ticker] = date

                consensus_match = re.search(r"(\w+) consensus is (\w+) \(avg score: (\d+\.\d+)\)", clean_log)
                if consensus_match:
                    ticker, cons, score = consensus_match.groups()
                    sentiment[ticker] = f"{cons} ({score})"

            # Prepare data for sorting
            today = datetime.now().date()
            table_data = []
            
            for ticker in self.config["stock_universe"]:
                earnings_date = earnings.get(ticker, "N/A")
                trade_time = trade_times.get(ticker, "N/A")
                sent = sentiment.get(ticker, "Pending")
                
                # Determine sentiment tag
                tag = "pending"
                if "Good" in sent:
                    tag = "good"
                elif "Mixed" in sent:
                    tag = "mixed"
                elif "Bad" in sent:
                    tag = "bad"
                
                # Determine if earnings date is past or future
                is_past = False
                sort_date = None
                if earnings_date != "N/A":
                    try:
                        date_obj = datetime.strptime(earnings_date, "%Y-%m-%d").date()
                        is_past = date_obj < today
                        sort_date = date_obj
                    except:
                        pass
                
                table_data.append({
                    "ticker": ticker,
                    "date": earnings_date,
                    "trade_time": trade_time,
                    "sentiment": sent,
                    "tag": tag,
                    "is_past": is_past,
                    "sort_date": sort_date
                })
            
            # Sort: upcoming (soonest first), then past (most recent first)
            def sort_key(item):
                if item["sort_date"] is None:
                    return (2, datetime.max.date())  # N/A dates go last
                elif item["is_past"]:
                    return (1, -item["sort_date"].toordinal())  # Past dates, newest first (negative for reverse)
                else:
                    return (0, item["sort_date"].toordinal())  # Upcoming dates, soonest first
            
            table_data.sort(key=sort_key)
            
            # Build sorted rows as plain text for comparison
            new_content = header
            for row in table_data:
                new_content += f"{row['ticker']:<10}{row['date']:<15}{row['trade_time']:<15}{row['sentiment']}\n"
            
            # Only update if content changed (prevents flashing)
            current_content = self.stock_table_text.get("1.0", tk.END)
            if new_content.strip() != current_content.strip():
                # Clear and rebuild with formatting
                self.stock_table_text.delete("1.0", tk.END)
                self.stock_table_text.insert(tk.END, header)
                
                for row in table_data:
                    # Ticker in cyan
                    self.stock_table_text.insert(tk.END, f"{row['ticker']:<10}", "cyan")
                    # Date in red if past, white if upcoming/N/A
                    date_tag = "red_date" if row["is_past"] else "white"
                    self.stock_table_text.insert(tk.END, f"{row['date']:<15}", date_tag)
                    # Trade time in white
                    self.stock_table_text.insert(tk.END, f"{row['trade_time']:<15}", "white")
                    # Sentiment with appropriate color
                    self.stock_table_text.insert(tk.END, row["sentiment"], row["tag"])
                    self.stock_table_text.insert(tk.END, "\n")
                
                # Update scrollbar visibility after content change
                self.update_scrollbar_visibility(self.stock_table_text, self.table_scrollbar)

        except Exception as e:
            print(f"Dashboard table update failed: {e}")

    def setup_visuals_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Visuals")

        # Configure grid weights for responsive layout
        tab.rowconfigure(0, weight=0)  # Top bar row
        tab.rowconfigure(1, weight=0)  # Stats row
        tab.rowconfigure(2, weight=2)  # Charts row (larger)
        tab.rowconfigure(3, weight=1)  # Table row
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        # Top bar frame
        top_frame = tk.Frame(tab, bg="black")
        top_frame.grid(row=0, column=0, columnspan=2, sticky="ew")
        top_frame.columnconfigure(1, weight=1)  # Center column expands
        
        # Settings button (right)
        settings_button = tk.Button(
            top_frame, text="⚙", bg="gray20", fg="white", command=lambda: self.open_settings("visuals_tab")
        )
        settings_button.grid(row=0, column=2, sticky="e", padx=5, pady=5)

        # === Top Stats Panel ===
        stats_frame = ttk.Frame(tab)
        stats_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=10)
        
        # Portfolio value
        self.portfolio_value_label = tk.Label(
            stats_frame, text="Portfolio: $0.00", font=("Consolas", 14, "bold"),
            bg="black", fg="cyan"
        )
        self.portfolio_value_label.pack(side="left", padx=20)
        
        # Total P&L
        self.total_pnl_label = tk.Label(
            stats_frame, text="Total P&L: $0.00", font=("Consolas", 14, "bold"),
            bg="black", fg="white"
        )
        self.total_pnl_label.pack(side="left", padx=20)
        
        # Win Rate
        self.win_rate_label = tk.Label(
            stats_frame, text="Win Rate: 0%", font=("Consolas", 14, "bold"),
            bg="black", fg="white"
        )
        self.win_rate_label.pack(side="left", padx=20)
        
        # Open Positions
        self.open_positions_label = tk.Label(
            stats_frame, text="Open: 0", font=("Consolas", 14, "bold"),
            bg="black", fg="yellow"
        )
        self.open_positions_label.pack(side="left", padx=20)

        # === Charts Row ===
        charts_frame = ttk.Frame(tab)
        charts_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=5, pady=5)
        charts_frame.columnconfigure(0, weight=1)
        charts_frame.columnconfigure(1, weight=1)
        charts_frame.rowconfigure(0, weight=1)

        # Left: Sentiment Chart
        sentiment_container = ttk.Frame(charts_frame)
        sentiment_container.grid(row=0, column=0, sticky="nsew", padx=5)
        
        self.sentiment_fig = plt.Figure(figsize=(6, 4), facecolor='#0a0a0a')
        self.sentiment_ax = self.sentiment_fig.add_subplot(111, facecolor='#0a0a0a')
        self.sentiment_canvas = FigureCanvasTkAgg(self.sentiment_fig, sentiment_container)
        self.sentiment_canvas.get_tk_widget().pack(fill="both", expand=True)

        # Right: Exposure Pie
        exposure_container = ttk.Frame(charts_frame)
        exposure_container.grid(row=0, column=1, sticky="nsew", padx=5)
        
        self.exposure_fig = plt.Figure(figsize=(6, 4), facecolor='#0a0a0a')
        self.exposure_ax = self.exposure_fig.add_subplot(111, facecolor='#0a0a0a')
        self.exposure_canvas = FigureCanvasTkAgg(self.exposure_fig, exposure_container)
        self.exposure_canvas.get_tk_widget().pack(fill="both", expand=True)

        # === Portfolio Table ===
        table_frame = tk.Frame(tab, bg='black')  # Changed to tk.Frame with black background
        table_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=10, pady=5)
        table_frame.rowconfigure(0, weight=1)
        table_frame.rowconfigure(1, weight=1)  # Added for tree
        table_frame.columnconfigure(0, weight=1)
        
        # Table header with info tooltip
        header_frame = tk.Frame(table_frame, bg="black")
        header_frame.grid(row=0, column=0, sticky="w", pady=(0, 5))
        
        table_label = tk.Label(
            header_frame, text="📊 Trade History", font=("Consolas", 12, "bold"),
            bg="black", fg="cyan"
        )
        table_label.pack(side="left")
        
        table_info = ttk.Label(header_frame, text="?", foreground="cyan", background="black", font=("Arial", 10))
        table_info.pack(side="left", padx=5)
        
        trade_history_tooltip = """Column Descriptions:
        
• Timestamp: Date and time the trade was executed
• Ticker: Stock symbol (e.g., AAPL, TSLA)
• Size: Number of shares traded
• Entry: Price per share when entering the position
• Current: Current price per share (updates in real-time)
• PnL: Profit/Loss in dollars ($)
• PnL%: Profit/Loss as a percentage of entry value
• Status: OPEN (active position) or CLOSED (exited position)"""
        
        ToolTip(table_info, trade_history_tooltip)
        
        self.portfolio_tree = ttk.Treeview(
            table_frame,
            columns=("Timestamp", "Ticker", "Size", "Entry", "Current", "PnL", "PnL%", "Status"),
            show="headings",
            style="Dark.Treeview",
            height=8
        )
        
        # Configure columns
        col_config = {
            "Timestamp": 140,
            "Ticker": 80,
            "Size": 80,
            "Entry": 80,
            "Current": 80,
            "PnL": 90,
            "PnL%": 80,
            "Status": 80
        }
        
        for col, width in col_config.items():
            self.portfolio_tree.heading(col, text=col)
            self.portfolio_tree.column(col, width=width, anchor="center")
        
        # Scrollbar for table
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.portfolio_tree.yview)
        self.portfolio_tree.configure(yscrollcommand=scrollbar.set)
        
        self.portfolio_tree.grid(row=1, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=1, sticky="ns")
        
        # Configure specific tags FIRST (with backgrounds and foregrounds)
        self.portfolio_tree.tag_configure("profit", foreground="green", background="black")
        self.portfolio_tree.tag_configure("loss", foreground="red", background="black")
        self.portfolio_tree.tag_configure("neutral", foreground="white", background="black")
        
        # THEN configure default empty tag (order matters!)
        self.portfolio_tree.tag_configure("", background="black", foreground="white")

        self.refresh_visuals()

    def open_log_file(self, file_path):
        """Open a log file in the system's default text editor."""
        try:
            import subprocess
            import platform
            if platform.system() == 'Windows':
                subprocess.Popen(['notepad', file_path])
            elif platform.system() == 'Darwin':  # macOS
                subprocess.Popen(['open', file_path])
            else:  # Linux
                subprocess.Popen(['xdg-open', file_path])
        except Exception as e:
            print(f"Failed to open log file: {e}")
    
    def setup_terminal_logs_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Terminal Logs")
        
        # Configure grid
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)

        # Top bar frame
        top_frame = tk.Frame(tab, bg="black")
        top_frame.grid(row=0, column=0, sticky="ew")
        top_frame.columnconfigure(1, weight=1)  # Center column expands
        
        # Open log file button (center)
        open_log_button = tk.Button(
            top_frame, text="📄 Open terminal_logs.txt", bg="gray20", fg="white", 
            command=lambda: self.open_log_file(LOG_PATH)
        )
        open_log_button.grid(row=0, column=1, pady=5)
        
        # Settings button (right)
        settings_button = tk.Button(
            top_frame, text="⚙", bg="gray20", fg="white", command=lambda: self.open_settings("logs_tab")
        )
        settings_button.grid(row=0, column=2, sticky="e", padx=5, pady=5)

        # Terminal logs (with ttk scrollbar like test 20)
        logs_frame = tk.Frame(tab, bg="black")
        logs_frame.grid(row=1, column=0, sticky="nsew")
        
        self.logs_text = tk.Text(
            logs_frame,
            wrap=tk.WORD,
            font=("Courier", self.preferences["logs_tab"].get("font_size", 10)),
            bg="black",
            fg="white",
            insertbackground="white",
            highlightbackground="black",
        )
        # Make read-only: prevent user editing while allowing programmatic updates
        self.logs_text.bind("<Key>", lambda e: "break")
        self.logs_scrollbar = ttk.Scrollbar(logs_frame, command=self.logs_text.yview)
        self.logs_text.config(yscrollcommand=self.logs_scrollbar.set)
        
        self.logs_text.pack(side='left', fill='both', expand=True)
        self.logs_scrollbar.pack(side='right', fill='y')
        
        # Auto-hide scrollbar when not needed
        self.logs_text.bind("<Configure>", lambda e: self.update_scrollbar_visibility(self.logs_text, self.logs_scrollbar))
        self.update_scrollbar_visibility(self.logs_text, self.logs_scrollbar)
        
        # Configure color tags
        ANSIParser.configure_tags(self.logs_text)

        # Start log updater
        threading.Thread(target=self.update_logs, daemon=True).start()

    def setup_grok_logs_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Grok Logs")
        
        # Configure grid
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)

        # Top bar frame
        top_frame = tk.Frame(tab, bg="black")
        top_frame.grid(row=0, column=0, sticky="ew")
        top_frame.columnconfigure(1, weight=1)  # Center column expands
        
        # Open log file button (center)
        open_log_button = tk.Button(
            top_frame, text="📄 Open grok_logs.json", bg="gray20", fg="white", 
            command=lambda: self.open_log_file(GROK_LOG_PATH)
        )
        open_log_button.grid(row=0, column=1, pady=5)
        
        # Settings button (right)
        settings_button = tk.Button(
            top_frame, text="⚙", bg="gray20", fg="white", command=lambda: self.open_settings("grok_logs_tab")
        )
        settings_button.grid(row=0, column=2, sticky="e", padx=5, pady=5)

        # Grok logs (with ttk scrollbar like test 20)
        grok_logs_frame = tk.Frame(tab, bg="black")
        grok_logs_frame.grid(row=1, column=0, sticky="nsew")
        
        self.grok_logs_text = tk.Text(
            grok_logs_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            bg="black",
            fg="white",
            insertbackground="white",
            highlightbackground="black",
        )
        # Make read-only: prevent user editing while allowing programmatic updates
        self.grok_logs_text.bind("<Key>", lambda e: "break")
        self.grok_logs_scrollbar = ttk.Scrollbar(grok_logs_frame, command=self.grok_logs_text.yview)
        self.grok_logs_text.config(yscrollcommand=self.grok_logs_scrollbar.set)
        
        self.grok_logs_text.pack(side='left', fill='both', expand=True)
        self.grok_logs_scrollbar.pack(side='right', fill='y')
        
        # Auto-hide scrollbar when not needed
        self.grok_logs_text.bind("<Configure>", lambda e: self.update_scrollbar_visibility(self.grok_logs_text, self.grok_logs_scrollbar))
        self.update_scrollbar_visibility(self.grok_logs_text, self.grok_logs_scrollbar)
        
        # Configure color tags for Grok logs
        self.grok_logs_text.tag_config("timestamp", foreground="magenta", font=("Consolas", 9, "bold"))
        self.grok_logs_text.tag_config("header", foreground="yellow", font=("Consolas", 10, "bold"))
        self.grok_logs_text.tag_config("ticker", foreground="cyan", font=("Consolas", 12, "bold"))
        self.grok_logs_text.tag_config("score_good", foreground="green", font=("Consolas", 11, "bold"))
        self.grok_logs_text.tag_config("score_mixed", foreground="orange", font=("Consolas", 11, "bold"))
        self.grok_logs_text.tag_config("score_bad", foreground="red", font=("Consolas", 11, "bold"))
        self.grok_logs_text.tag_config("label", foreground="light gray", font=("Consolas", 9, "bold"))
        self.grok_logs_text.tag_config("reasoning", foreground="white")
        self.grok_logs_text.tag_config("sources", foreground="light gray", font=("Consolas", 9, "italic"))
        self.grok_logs_text.tag_config("separator", foreground="gray40")
        self.grok_logs_text.tag_config("prompt", foreground="light blue", font=("Consolas", 9))

        # Initial load
        self.update_grok_logs()

    def setup_app_logs_tab(self):
        """Setup App Logs tab to show real-time app events."""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="App Logs")
        
        # Configure grid
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)

        # Top bar frame
        top_frame = tk.Frame(tab, bg="black")
        top_frame.grid(row=0, column=0, sticky="ew")
        top_frame.columnconfigure(1, weight=1)  # Center column expands
        
        # Open log file button (center)
        open_log_button = tk.Button(
            top_frame, text="📄 Open app_logs.txt", bg="gray20", fg="white", 
            command=lambda: self.open_log_file(APP_LOG_PATH)
        )
        open_log_button.grid(row=0, column=1, pady=5)
        
        # Settings button (right)
        settings_button = tk.Button(
            top_frame, text="⚙", bg="gray20", fg="white", command=lambda: self.open_settings("app_logs_tab")
        )
        settings_button.grid(row=0, column=2, sticky="e", padx=5, pady=5)

        # App logs (with ttk scrollbar like test 20)
        app_logs_frame = tk.Frame(tab, bg="black")
        app_logs_frame.grid(row=1, column=0, sticky="nsew")
        
        self.app_logs_text = tk.Text(
            app_logs_frame,
            wrap=tk.WORD,
            font=("Courier", 9),
            bg="black",
            fg="white",
            insertbackground="white",
            highlightbackground="black",
        )
        # Make read-only: prevent user editing while allowing programmatic updates
        self.app_logs_text.bind("<Key>", lambda e: "break")
        self.app_logs_scrollbar = ttk.Scrollbar(app_logs_frame, command=self.app_logs_text.yview)
        self.app_logs_text.config(yscrollcommand=self.app_logs_scrollbar.set)
        
        self.app_logs_text.pack(side='left', fill='both', expand=True)
        self.app_logs_scrollbar.pack(side='right', fill='y')
        
        # Auto-hide scrollbar when not needed
        self.app_logs_text.bind("<Configure>", lambda e: self.update_scrollbar_visibility(self.app_logs_text, self.app_logs_scrollbar))
        self.update_scrollbar_visibility(self.app_logs_text, self.app_logs_scrollbar)
        
        # Configure color tags for different log levels
        self.app_logs_text.tag_config("INFO", foreground="cyan")
        self.app_logs_text.tag_config("SUCCESS", foreground="green")
        self.app_logs_text.tag_config("WARNING", foreground="yellow")
        self.app_logs_text.tag_config("ERROR", foreground="red")
        self.app_logs_text.tag_config("DEBUG", foreground="gray")
        self.app_logs_text.tag_config("timestamp", foreground="magenta")
        
        # Log initial app startup
        self.log_app_event("INFO", "App Logs initialized")
        self.log_app_event("SUCCESS", "CDEM Trading Agent UI started successfully")
        # IMPORTANT: Log both versions separately
        self.log_app_event("INFO", f"App UI Version: {self.get_app_version()}")
        self.log_app_event("INFO", f"CDEM Agent Version: {self.get_agent_version()}")
        self.log_app_event("INFO", f"Config loaded: {len(self.config.get('stock_universe', []))} tickers in universe")
        self.log_app_event("INFO", f"Paper Trading: {'Enabled' if self.config.get('paper_trading', True) else 'Disabled'}")
        self.log_app_event("SUCCESS", "Multi-threaded updates enabled for smooth UI performance")
        self.log_app_event("INFO", "Config page organized into 5 sections: System, Risk, Exit, Idle Capital, Options")
        self.log_app_event("INFO", f"Idle capital target: {self.config.get('idle_capital_target', 'SPY')}")

    def log_app_event(self, level, message):
        """Log an app event with timestamp and color coding to both UI and file."""
        try:
            from datetime import datetime
            timestamp_short = datetime.now().strftime("%H:%M:%S")
            timestamp_full = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Write to file
            try:
                with open(APP_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(f"[{timestamp_full}] [{level}] {message}\n")
            except Exception as e:
                print(f"Failed to write to app log file: {e}")
            
            # Insert to UI
            self.app_logs_text.insert(tk.END, f"[{timestamp_short}] ", "timestamp")
            self.app_logs_text.insert(tk.END, f"[{level}] ", level)
            self.app_logs_text.insert(tk.END, f"{message}\n")
            
            # Auto-scroll to bottom
            self.app_logs_text.see(tk.END)
            
            # Keep only last 1000 lines in UI
            lines = int(self.app_logs_text.index('end-1c').split('.')[0])
            if lines > 1000:
                self.app_logs_text.delete('1.0', f'{lines-1000}.0')
            
            # Update scrollbar visibility after content change
            self.update_scrollbar_visibility(self.app_logs_text, self.app_logs_scrollbar)
        except Exception as e:
            # Silently fail if logging doesn't work
            print(f"App logging error: {e}")

    def update_grok_logs(self):
        if os.path.exists(GROK_LOG_PATH):
            try:
                with open(GROK_LOG_PATH, "r") as f:
                    data = json.load(f)
                
                self.grok_logs_text.delete("1.0", tk.END)
                
                # Display entries in reverse order (most recent first)
                for entry in reversed(data):
                    # Format timestamp
                    timestamp = entry.get("timestamp", "Unknown")
                    try:
                        dt = datetime.fromisoformat(timestamp)
                        formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        formatted_time = timestamp
                    
                    self.grok_logs_text.insert(tk.END, "=" * 80 + "\n", "separator")
                    self.grok_logs_text.insert(tk.END, f"📅 {formatted_time}\n", "timestamp")
                    
                    # Check if this is a summary entry (has ticker and scores)
                    if "ticker" in entry and "scores" in entry:
                        ticker = entry["ticker"]
                        scores = entry["scores"]
                        avg_score = entry.get("avg_score", sum(scores) / len(scores) if scores else 0)
                        
                        self.grok_logs_text.insert(tk.END, "\n", "")
                        self.grok_logs_text.insert(tk.END, f"🎯 TICKER: ", "label")
                        self.grok_logs_text.insert(tk.END, f"{ticker}\n", "ticker")
                        
                        # Determine color based on average score
                        if avg_score >= 70:
                            score_tag = "score_good"
                            classification = "GOOD"
                        elif avg_score >= 40:
                            score_tag = "score_mixed"
                            classification = "MIXED"
                        else:
                            score_tag = "score_bad"
                            classification = "BAD"
                        
                        self.grok_logs_text.insert(tk.END, f"   Classification: ", "label")
                        self.grok_logs_text.insert(tk.END, f"{classification}\n", score_tag)
                        
                        self.grok_logs_text.insert(tk.END, f"   Average Score: ", "label")
                        self.grok_logs_text.insert(tk.END, f"{avg_score:.2f}\n", score_tag)
                        
                        self.grok_logs_text.insert(tk.END, f"   Individual Scores: ", "label")
                        self.grok_logs_text.insert(tk.END, f"{scores}\n", "white")
                    
                    # Check if this is a prompt/response entry
                    elif "sent" in entry or "received" in entry:
                        if "sent" in entry:
                            prompt = entry["sent"]
                            # Show abbreviated prompt
                            prompt_preview = prompt[:200] + "..." if len(prompt) > 200 else prompt
                            self.grok_logs_text.insert(tk.END, "\n📤 SENT TO GROK:\n", "header")
                            self.grok_logs_text.insert(tk.END, f"{prompt_preview}\n", "prompt")
                        
                        if "received" in entry:
                            self.grok_logs_text.insert(tk.END, "\n📥 RECEIVED FROM GROK:\n", "header")
                            response = entry["received"]
                            
                            # Try to parse as JSON
                            try:
                                response_data = json.loads(response)
                                
                                if "classification" in response_data:
                                    classification = response_data["classification"]
                                    score = response_data.get("score", 0)
                                    
                                    # Determine score tag
                                    if score >= 70:
                                        score_tag = "score_good"
                                    elif score >= 40:
                                        score_tag = "score_mixed"
                                    else:
                                        score_tag = "score_bad"
                                    
                                    self.grok_logs_text.insert(tk.END, f"   Classification: ", "label")
                                    self.grok_logs_text.insert(tk.END, f"{classification}\n", score_tag)
                                    
                                    self.grok_logs_text.insert(tk.END, f"   Score: ", "label")
                                    self.grok_logs_text.insert(tk.END, f"{score}\n", score_tag)
                                
                                if "reasoning" in response_data:
                                    reasoning = response_data["reasoning"]
                                    # Wrap reasoning for readability (use configurable width)
                                    text_width = self.preferences.get("grok_logs_tab", {}).get("response_text_width", 70)
                                    wrapped_reasoning = "\n      ".join([reasoning[i:i+text_width] for i in range(0, len(reasoning), text_width)])
                                    self.grok_logs_text.insert(tk.END, f"   Reasoning:\n", "label")
                                    self.grok_logs_text.insert(tk.END, f"      {wrapped_reasoning}\n", "reasoning")
                                
                                if "sources" in response_data:
                                    sources = response_data["sources"]
                                    self.grok_logs_text.insert(tk.END, f"   Sources: ", "label")
                                    self.grok_logs_text.insert(tk.END, f"{', '.join(sources)}\n", "sources")
                                    
                            except json.JSONDecodeError:
                                # If not valid JSON, show raw response
                                self.grok_logs_text.insert(tk.END, f"{response[:500]}...\n", "white")
                    
                    self.grok_logs_text.insert(tk.END, "\n", "")
                
                # Update scrollbar visibility after content change
                self.update_scrollbar_visibility(self.grok_logs_text, self.grok_logs_scrollbar)
                
            except json.JSONDecodeError:
                self.grok_logs_text.delete("1.0", tk.END)
                self.grok_logs_text.insert(tk.END, "Invalid JSON in grok_logs.json", "score_bad")
                self.update_scrollbar_visibility(self.grok_logs_text, self.grok_logs_scrollbar)
            except Exception as e:
                self.grok_logs_text.delete("1.0", tk.END)
                self.grok_logs_text.insert(tk.END, f"Error loading grok_logs.json: {str(e)}", "score_bad")
                self.update_scrollbar_visibility(self.grok_logs_text, self.grok_logs_scrollbar)

    def start_agent(self):
        """Start the CDEM agent as a subprocess with output piping."""
        if self.running:
            return
        try:
            self.log_app_event("INFO", "Starting CDEM agent...")
            self.running = True
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["FORCE_COLOR"] = "1"  # Force termcolor to add ANSI even in pipes

            self.agent_process = subprocess.Popen(
                ["python", "-u", AGENT_SCRIPT],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            if self.agent_process.poll() is not None:
                raise RuntimeError("Agent failed to start")
            threading.Thread(target=self.read_agent_output, daemon=True).start()
            self.status_label.config(text="Agent Status: Running", foreground="green")
            self.start_blink()  # Start blinking when running
            self.log_app_event("SUCCESS", "Agent started successfully")
        except (OSError, FileNotFoundError, RuntimeError) as e:
            self.log_app_event("ERROR", f"Failed to start agent: {str(e)}")
            messagebox.showerror("Error", f"Failed to start agent: {str(e)}")
            self.running = False

    def stop_agent(self):
        if not self.running:
            return
        self.log_app_event("INFO", "Stopping CDEM agent...")
        self.running = False
        self.stop_blink()  # Stop blinking
        if self.agent_process:
            # Send Ctrl+C (SIGINT)
            try:
                ctypes.windll.kernel32.GenerateConsoleCtrlEvent(0, self.agent_process.pid)
                self.agent_process.wait(timeout=5)  # Wait for graceful shutdown
                self.log_app_event("SUCCESS", "Agent stopped gracefully")
            except subprocess.TimeoutExpired:
                self.agent_process.terminate()  # Fallback
                self.log_app_event("WARNING", "Agent terminated forcefully (timeout)")
            self.agent_process.wait()
        self.status_label.config(text="Agent Status: Stopped", foreground="red")

    def start_blink(self):
        if not self.blinking:
            self.blinking = True
            self.blink_status()

    def stop_blink(self):
        self.blinking = False

    def blink_status(self):
        if not self.blinking:
            return
        current_fg = self.status_label.cget("foreground")
        new_fg = "black" if current_fg == "green" else "green"
        self.status_label.config(foreground=new_fg)
        self.root.after(500, self.blink_status)  # Blink every 500ms

    def read_agent_output(self):
        try:
            for line in iter(self.agent_process.stdout.readline, ""):
                self.log_queue.put(line.strip())
        except UnicodeDecodeError as e:
            print(f"Decode error in output: {e}")
        except Exception as e:
            print(f"Output read error: {e}")

    def update_logs(self):
        """Continuously update log displays with colored output from agent."""
        # Initialize color state (persists across lines like terminal behavior)
        if not hasattr(self, '_log_color'):
            self._log_color = "white"
        if not hasattr(self, '_dashboard_color'):
            self._dashboard_color = "white"
        
        while True:
            try:
                line = self.log_queue.get_nowait()
                self.all_logs.append(line)  # Store raw for parsing

                # Split by ANSI codes (Method 7: Accumulate)
                parts = re.split(r'(\x1b\[\d+m)', line)
                
                # Process for Terminal Logs (with timestamp)
                timestamp = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                self.logs_text.insert(tk.END, timestamp, "white")
                
                for part in parts:
                    if re.match(r'\x1b\[\d+m', part):
                        # Extract color code
                        code_match = re.search(r'\d+', part)
                        if code_match:
                            code = code_match.group()
                            self._log_color = ANSIParser.COLOR_MAP.get(code, "white")
                    elif part:
                        # Insert text with current color
                        self.logs_text.insert(tk.END, part, self._log_color)
                
                self.logs_text.insert(tk.END, "\n", "white")
                
                # Check auto-scroll preference for terminal logs
                if self.preferences.get("logs_tab", {}).get("auto_scroll", True):
                    self.logs_text.see(tk.END)
                
                # Update scrollbar visibility
                self.update_scrollbar_visibility(self.logs_text, self.logs_scrollbar)
                
                # Process for Dashboard (without timestamp, separate color state)
                for part in parts:
                    if re.match(r'\x1b\[\d+m', part):
                        # Extract color code
                        code_match = re.search(r'\d+', part)
                        if code_match:
                            code = code_match.group()
                            self._dashboard_color = ANSIParser.COLOR_MAP.get(code, "white")
                    elif part:
                        # Insert text with current color
                        self.dashboard_logs_text.insert(tk.END, part, self._dashboard_color)
                
                self.dashboard_logs_text.insert(tk.END, "\n", "white")
                
                # Check auto-scroll preference for dashboard logs
                if self.preferences.get("dashboard_tab", {}).get("auto_scroll", True):
                    self.dashboard_logs_text.see(tk.END)
                
                # Update scrollbar visibility
                self.update_scrollbar_visibility(self.dashboard_logs_text, self.dashboard_logs_scrollbar)
                
            except queue.Empty:
                pass
            time.sleep(0.1)

    def refresh_visuals(self):
        """Launch threaded refresh to avoid blocking UI."""
        threading.Thread(target=self._refresh_visuals_background, daemon=True).start()
    
    def _refresh_visuals_background(self):
        """Background thread: Fetch all data without blocking UI."""
        try:
            # Get real portfolio value from Tradier
            try:
                import requests
                from dotenv import load_dotenv
                load_dotenv()
                
                # Determine which account to use based on paper_trading setting
                is_paper = self.config.get("paper_trading", True)
                
                if is_paper:
                    api_key = os.getenv("TRADIER_PAPER_API_KEY")
                    account_id = os.getenv("TRADIER_PAPER_ACCOUNT_ID")
                    base_url = "https://sandbox.tradier.com/v1"
                else:
                    api_key = os.getenv("TRADIER_API_KEY")
                    account_id = os.getenv("TRADIER_ACCOUNT_ID")
                    base_url = "https://api.tradier.com/v1"
                
                if api_key and account_id:
                    response = requests.get(
                        f"{base_url}/accounts/{account_id}/balances",
                        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                        timeout=15
                    )
                    if response.status_code == 200:
                        data = response.json()
                        balances = data.get("balances", {})
                        portfolio_value = float(balances.get("total_equity", 100000.0))
                        cash = float(balances.get("total_cash", 100000.0))
                    else:
                        portfolio_value = 100000.0
                        cash = 100000.0
                else:
                    portfolio_value = 100000.0
                    cash = 100000.0
            except Exception as e:
                print(f"Tradier connection error: {e}")
                portfolio_value = 100000.0
                cash = 100000.0

            # === Calculate Metrics from Trade History ===
            total_pnl = 0.0
            wins = 0
            losses = 0
            open_positions = 0
            portfolio_df = None
            
            if os.path.exists(PORTFOLIO_CSV):
                portfolio_df = pd.read_csv(PORTFOLIO_CSV)
                
                # Calculate stats
                closed_trades = portfolio_df[portfolio_df["status"] == "closed"]
                open_trades = portfolio_df[portfolio_df["status"] == "open"]
                
                if not closed_trades.empty:
                    total_pnl = closed_trades["pnl"].sum()
                    wins = len(closed_trades[closed_trades["pnl"] > 0])
                    losses = len(closed_trades[closed_trades["pnl"] < 0])
                
                open_positions = len(open_trades)
            
            # Calculate derived values
            win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
            pnl_color = "green" if total_pnl > 0 else "red" if total_pnl < 0 else "white"

            # === Prepare Sentiment Chart Data ===
            sentiment_df = None
            if os.path.exists(SENTIMENT_CSV):
                try:
                    df = pd.read_csv(SENTIMENT_CSV)
                    time_frame = self.preferences["visuals_tab"].get("sentiment_time_frame_days", 30)
                    start_date = datetime.now() - timedelta(days=time_frame)
                    df["timestamp"] = pd.to_datetime(df["timestamp"])
                    sentiment_df = df[df["timestamp"] > start_date]
                except Exception as e:
                    print(f"Sentiment data error: {e}")

            # === Prepare Portfolio Table Data ===
            table_rows = []
            if portfolio_df is not None:
                try:
                    for i, row in portfolio_df.iterrows():
                        # Calculate P&L percentage
                        pnl = row.get("pnl", 0)
                        entry_price = row.get("entry_price", 0)
                        position_size = row.get("position_size", 0)
                        
                        if entry_price > 0 and position_size > 0:
                            pnl_pct = (pnl / (entry_price * position_size)) * 100
                        else:
                            pnl_pct = 0
                        
                        # Determine row color tag
                        if pnl > 0:
                            tag = "profit"
                        elif pnl < 0:
                            tag = "loss"
                        else:
                            tag = "neutral"
                        
                        # Format values
                        timestamp = pd.to_datetime(row["timestamp"]).strftime("%Y-%m-%d %H:%M")
                        values = (
                            timestamp,
                            row["ticker"],
                            f"{row['position_size']:.2f}",
                            f"${row['entry_price']:.2f}",
                            f"${row['current_price']:.2f}",
                            f"${pnl:.2f}",
                            f"{pnl_pct:.2f}%",
                            row["status"].upper()
                        )
                        table_rows.append((values, tag))
                except Exception as e:
                    print(f"Portfolio table processing error: {e}")

            # === Prepare Exposure Pie Chart Data ===
            active_value = 0.0
            spy_value = 0.0
            cash_value = cash
            
            if portfolio_df is not None:
                try:
                    open_df = portfolio_df[portfolio_df["status"] == "open"]
                    if not open_df.empty:
                        active_value = (open_df["position_size"] * open_df["current_price"]).sum()
                except Exception as e:
                    print(f"Exposure calculation error: {e}")
            
            # Calculate SPY value (remaining portfolio value not in active trades or cash)
            spy_value = max(0, portfolio_value - active_value - cash_value)
            
            # Prepare pie chart data
            pie_values = []
            pie_labels = []
            pie_colors = []
            
            if active_value > 0:
                pie_values.append(active_value)
                pie_labels.append(f"Active Trades\n${active_value:,.0f}")
                pie_colors.append('#ff4444')
            
            if spy_value > 0:
                pie_values.append(spy_value)
                pie_labels.append(f"SPY/Idle\n${spy_value:,.0f}")
                pie_colors.append('#4444ff')
            
            if cash_value > 0:
                pie_values.append(cash_value)
                pie_labels.append(f"Cash\n${cash_value:,.0f}")
                pie_colors.append('#44ff44')
            
            # Now schedule UI updates on main thread
            self.root.after(0, lambda: self._update_visuals_ui(
                portfolio_value, win_rate, wins, losses, total_pnl, pnl_color, 
                open_positions, sentiment_df, table_rows, pie_values, pie_labels, pie_colors
            ))
            
        except Exception as e:
            print(f"Refresh visuals error: {e}")
        
        # Schedule next refresh using preference setting
        refresh_interval_ms = self.preferences["visuals_tab"].get("refresh_interval", 10) * 1000
        self.root.after(refresh_interval_ms, self.refresh_visuals)
    
    def _update_visuals_ui(self, portfolio_value, win_rate, wins, losses, total_pnl, 
                           pnl_color, open_positions, sentiment_df, table_rows, 
                           pie_values, pie_labels, pie_colors):
        """Update UI elements on main thread with pre-computed data."""
        try:
            # Update labels
            self.portfolio_value_label.config(text=f"Portfolio: ${portfolio_value:,.2f}")
            self.win_rate_label.config(text=f"Win Rate: {win_rate:.1f}% ({wins}W/{losses}L)")
            self.total_pnl_label.config(text=f"Total P&L: ${total_pnl:,.2f}", fg=pnl_color)
            self.open_positions_label.config(text=f"Open: {open_positions}")
            
            # Update sentiment chart
            if sentiment_df is not None and not sentiment_df.empty:
                self.sentiment_ax.clear()
                self.sentiment_ax.plot(sentiment_df["timestamp"], sentiment_df["sentiment_score"], 
                                     color="cyan", linewidth=2, marker='o', 
                                     markersize=4, alpha=0.8)
                self.sentiment_ax.axhline(y=70, color='green', linestyle='--', alpha=0.3, linewidth=1)
                self.sentiment_ax.axhline(y=40, color='red', linestyle='--', alpha=0.3, linewidth=1)
                self.sentiment_ax.set_title("Sentiment Scores Over Time", 
                                           color='white', fontsize=12, fontweight='bold', pad=10)
                self.sentiment_ax.set_xlabel("Date", color='gray', fontsize=10)
                self.sentiment_ax.set_ylabel("Score", color='gray', fontsize=10)
                self.sentiment_ax.tick_params(colors='gray', labelsize=8)
                self.sentiment_ax.grid(True, alpha=0.15, color='gray')
                self.sentiment_fig.autofmt_xdate()
                self.sentiment_canvas.draw()
            
            # Update portfolio table
            for item in self.portfolio_tree.get_children():
                self.portfolio_tree.delete(item)
            for values, tag in table_rows:
                self.portfolio_tree.insert("", "end", values=values, tags=(tag, ""))
            
            # Update exposure pie chart
            self.exposure_ax.clear()
            if pie_values:
                wedges, texts, autotexts = self.exposure_ax.pie(
                    pie_values,
                    labels=pie_labels,
                    autopct='%1.1f%%',
                    colors=pie_colors,
                    startangle=90,
                    textprops={'color': 'white', 'fontsize': 10}
                )
                for autotext in autotexts:
                    autotext.set_color('white')
                    autotext.set_fontweight('bold')
                    autotext.set_fontsize(9)
            self.exposure_ax.set_title("Portfolio Allocation", 
                                      color='white', fontsize=12, fontweight='bold', pad=10)
            self.exposure_canvas.draw()
            
        except Exception as e:
            print(f"UI update error: {e}")

    def start_file_watcher(self):
        # Watch CSV files in data directory
        event_handler = FileWatcherHandler(self.refresh_visuals)
        observer = Observer()
        observer.schedule(event_handler, path="src/data/cdem", recursive=False)
        observer.start()
        
        # Watch grok_logs.json in logs directory
        grok_handler = GrokLogWatcherHandler(self.update_grok_logs)
        grok_observer = Observer()
        grok_observer.schedule(grok_handler, path="logs", recursive=False)
        grok_observer.start()


if __name__ == "__main__":
    root = tk.Tk()
    app = CDEMApp(root)
    root.mainloop()
