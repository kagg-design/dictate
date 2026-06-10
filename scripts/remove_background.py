import os
import sys
from PIL import Image

def remove_background(img_path, output_png_path, tolerance=50):
    print(f"Opening source image: {img_path}")
    if not os.path.exists(img_path):
        print("Source image does not exist!")
        return False
        
    try:
        img = Image.open(img_path).convert("RGBA")
        width, height = img.size
        pixels = img.load()
        
        # Sample background color from the top-left corner
        bg_color = pixels[0, 0]
        print(f"Sampled corner color: {bg_color}")
        
        # Keep track of visited pixels to avoid infinite loops
        visited = [[False for _ in range(height)] for _ in range(width)]
        
        # Start queue with the 4 corners
        queue = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]
        for x, y in queue:
            visited[x][y] = True
            
        def is_similar(c1, c2):
            # Euclidean distance in RGB space
            return ((c1[0] - c2[0])**2 + (c1[1] - c2[1])**2 + (c1[2] - c2[2])**2)**0.5 < tolerance

        print("Executing flood fill to detect external black background...")
        count = 0
        head = 0
        # Using a simple list as queue. For 1024x1024 it's fast enough if we don't do too many appends
        # To make it super fast, we use a classic BFS list with a pointer (head)
        while head < len(queue):
            cx, cy = queue[head]
            head += 1
            
            # Make the background pixel transparent
            # Maintain the color but set alpha to 0
            pixels[cx, cy] = (0, 0, 0, 0)
            count += 1
            
            # Check 4 directions
            for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < width and 0 <= ny < height:
                    if not visited[nx][ny]:
                        # Check color similarity
                        if is_similar(pixels[nx, ny], bg_color):
                            visited[nx][ny] = True
                            queue.append((nx, ny))
                            
        print(f"Cleared {count} background pixels.")
        img.save(output_png_path, "PNG")
        print(f"Transparent PNG saved to: {output_png_path}")
        return True
    except Exception as e:
        print(f"Error during background removal: {e}")
        return False

def convert_png_to_ico(png_path, ico_output_path):
    try:
        img = Image.open(png_path)
        sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
        print(f"Saving multi-size transparent ICO to: {ico_output_path}")
        img.save(ico_output_path, format='ICO', sizes=sizes)
        print("Transparent ICO successfully created.")
        return True
    except Exception as e:
        print(f"Error during ICO conversion: {e}")
        return False

if __name__ == "__main__":
    src_png = r"C:\Users\igerg\.gemini\antigravity\brain\6273c716-7681-49f7-be48-945984c90da1\microphone_icon_source_1781106291236.png"
    temp_png = r"c:\laragon\www\Projects\dictate\icon_transparent.png"
    dest_ico = r"c:\laragon\www\Projects\dictate\icon.ico"
    dest_png = r"c:\laragon\www\Projects\dictate\icon.png"
    
    # Remove background and save to temp_png
    if remove_background(src_png, temp_png, tolerance=60):
        # Overwrite icon.png with the transparent version
        if os.path.exists(dest_png):
            os.remove(dest_png)
        os.rename(temp_png, dest_png)
        print("Updated icon.png to transparent version.")
        
        # Generate new transparent icon.ico
        convert_png_to_ico(dest_png, dest_ico)
