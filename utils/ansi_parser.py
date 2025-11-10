"""
ANSI Color Code Parser for Terminal Output

Extracts and maps ANSI escape codes to Tkinter color tags for GUI display.
"""

import re
from typing import Tuple, Optional


class ANSIParser:
    """Parse ANSI color codes from terminal output and convert to tags."""

    # ANSI color code to tag name mapping
    COLOR_MAP = {
        "30": "black",
        "31": "red",
        "32": "green",
        "33": "yellow",
        "34": "blue",
        "35": "magenta",
        "36": "cyan",
        "37": "white",
        "90": "grey",
        "91": "light_red",
        "92": "light_green",
        "93": "light_yellow",
        "94": "light_blue",
        "95": "light_magenta",
        "96": "light_cyan",
        "97": "bright_white",
        "0": "white",  # reset
    }

    @staticmethod
    def parse_line(line: str) -> Tuple[str, str]:
        """
        Parse a line with ANSI codes and return cleaned text and color tag.
        
        DEPRECATED: Use parse_line_segments for better color support.

        Args:
            line: Raw line from terminal with potential ANSI codes

        Returns:
            Tuple of (clean_text, color_tag)
            clean_text: Line with ANSI codes removed
            color_tag: Tkinter tag name for coloring
        """
        # Try to find the first ANSI color code in the line
        # Pattern: \x1b[<code>m<text>\x1b[0m or just \x1b[<code>m at start
        ansi_match = re.search(r"\x1b\[(\d+)m", line)
        
        if ansi_match:
            code = ansi_match.group(1)
            tag = ANSIParser.COLOR_MAP.get(code, "white")
        else:
            tag = "white"

        # Strip all ANSI codes to get clean text
        clean_line = re.sub(r"\x1b\[\d+m", "", line)
        return clean_line, tag

    @staticmethod
    def parse_line_segments(line: str):
        """
        Parse a line with ANSI codes into colored segments.
        
        Yields tuples of (text, color_tag) for each colored segment in the line.
        This allows multiple colors per line to be properly displayed.
        
        Args:
            line: Raw line from terminal with potential ANSI codes
            
        Yields:
            Tuple of (text, color_tag) for each segment
        """
        if not line:
            yield ("", "white")
            return
            
        # Split by ANSI escape codes
        # Pattern: \x1b[<number>m
        pattern = r'\x1b\[(\d+)m'
        
        parts = re.split(pattern, line)
        current_tag = "white"
        
        for i, part in enumerate(parts):
            if i % 2 == 0:
                # This is text content (not a code)
                if part:  # Only yield non-empty text
                    yield (part, current_tag)
            else:
                # This is an ANSI code number
                current_tag = ANSIParser.COLOR_MAP.get(part, "white")
        
        # If we didn't yield anything, yield empty white
        if not any(parts[::2]):  # Check if any text parts exist
            yield ("", "white")

    @staticmethod
    def strip_ansi(text: str) -> str:
        """
        Remove all ANSI escape codes from text.

        Args:
            text: Text potentially containing ANSI codes

        Returns:
            Clean text without ANSI codes
        """
        return re.sub(r"\x1b\[\d+m", "", text)

    @staticmethod
    def configure_tags(text_widget):
        """
        Configure color tags for a Tkinter Text widget.

        Args:
            text_widget: Tkinter Text or ScrolledText widget
        """
        text_widget.tag_config("black", foreground="black")
        text_widget.tag_config("red", foreground="red")
        text_widget.tag_config("green", foreground="green")
        text_widget.tag_config("yellow", foreground="yellow")
        text_widget.tag_config("blue", foreground="blue")
        text_widget.tag_config("magenta", foreground="magenta")
        text_widget.tag_config("cyan", foreground="cyan")
        text_widget.tag_config("white", foreground="white")
        text_widget.tag_config("grey", foreground="grey")
        text_widget.tag_config("light_red", foreground="#FF6B6B")
        text_widget.tag_config("light_green", foreground="lightgreen")
        text_widget.tag_config("light_yellow", foreground="#FFEB3B")
        text_widget.tag_config("light_blue", foreground="lightblue")
        text_widget.tag_config("light_magenta", foreground="#FF6BFF")
        text_widget.tag_config("light_cyan", foreground="lightcyan")
        text_widget.tag_config("bright_white", foreground="#FFFFFF")
