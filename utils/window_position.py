"""
Window Position Persistence Utility
Saves and restores window position/size for consistent placement
"""

import json
import os


class WindowPositionManager:
    def __init__(self, config_file="window_position.json"):
        """Initialize window position manager with config file path"""
        self.config_file = config_file
        self.default_geometry = {
            "x": 100,
            "y": 100,
            "width": 1400,
            "height": 900
        }
    
    def save_position(self, root):
        """Save current window position and size"""
        try:
            # Get current geometry
            geometry = root.geometry()  # Format: "WIDTHxHEIGHT+X+Y"
            
            # Parse geometry string
            size_pos = geometry.split('+')
            width_height = size_pos[0].split('x')
            
            position_data = {
                "width": int(width_height[0]),
                "height": int(width_height[1]),
                "x": int(size_pos[1]) if len(size_pos) > 1 else 100,
                "y": int(size_pos[2]) if len(size_pos) > 2 else 100
            }
            
            # Save to file
            with open(self.config_file, 'w') as f:
                json.dump(position_data, f, indent=4)
            
        except Exception as e:
            pass  # Silently fail - errors are handled by caller
    
    def load_position(self):
        """Load saved window position, or return default if not found"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    position_data = json.load(f)
                
                # Validate data
                required_keys = ['x', 'y', 'width', 'height']
                if all(key in position_data for key in required_keys):
                    return position_data
            
            # Return default if file doesn't exist or invalid
            return self.default_geometry
            
        except Exception as e:
            print(f"Error loading window position: {e}")
            return self.default_geometry
    
    def apply_position(self, root):
        """Apply saved position to window"""
        position = self.load_position()
        geometry_string = f"{position['width']}x{position['height']}+{position['x']}+{position['y']}"
        root.geometry(geometry_string)

