import time
import pyperclip
import keyboard
from src.logger import logger

def paste_text(text):
    """
    Inserts text into the currently focused window.
    Saves the existing clipboard, copies the new text, sends Ctrl+V, 
    waits briefly, and restores the original clipboard content.
    """
    if not text:
        logger.info("No text to insert. Skipping paste operation.")
        return

    logger.info(f"Inserting text: '{text[:40]}...' (total length: {len(text)})")
    
    # Save current clipboard contents
    try:
        old_clipboard = pyperclip.paste()
    except Exception as e:
        logger.warning(f"Could not read from clipboard: {e}. Proceeding without recovery.")
        old_clipboard = None

    # Copy the transcribed text to clipboard
    try:
        pyperclip.copy(text)
    except Exception as e:
        logger.error(f"Failed to copy transcribed text to clipboard: {e}")
        return

    # Ensure no modifier keys are logically stuck/pressed in the OS before pasting
    for key in ['left windows', 'right windows', 'ctrl', 'shift', 'alt']:
        try:
            keyboard.release(key)
        except Exception:
            pass
    time.sleep(0.05)

    # Send global Ctrl+V keystroke to trigger native paste
    try:
        logger.debug("Sending Ctrl+V keystroke...")
        keyboard.send('ctrl+v')
    except Exception as e:
        logger.error(f"Failed to simulate Ctrl+V keystroke: {e}")
        return

    # Short delay to allow the active application to complete the paste action
    time.sleep(0.25)

    # Restore the previous clipboard content
    try:
        if old_clipboard:
            pyperclip.copy(old_clipboard)
            logger.debug("Previous clipboard content successfully restored.")
        else:
            pyperclip.copy('')
    except Exception as e:
        logger.warning(f"Failed to restore original clipboard content: {e}")
