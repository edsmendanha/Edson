import time
import json

# ... other code ...

def connect_iq():
    try:
        # Simpler connection logic without hard timeout threads
        # Assume `establish_connection()` is the function that connects 
        if establish_connection():
            time.sleep(1)  # Add a short sleep after successful connect
            change_balance()
        else:
            raise Exception("Connection failed.")
    except json.JSONDecodeError:
        # Handle JSONDecodeError
        print('Failed to decode JSON, retrying...')
        connect_iq()  # Retry connection
    except Exception as e:
        print(f'An error occurred: {e}')
        connect_iq()  # Retry connection


def ensure_connected():
    try:
        # Ensure connection functionality
        connect_iq()
    except Exception as e:
        print(f'Ensure connected error: {e}')

# Configs
CONFIG = {
    'LOGS': {
        'enable_ws_on_message_patch': True  # Default value
    }
}

# Only apply the patch after a successful connect
if connection_successful:
    apply_ws_on_message_patch(CONFIG['LOGS']['enable_ws_on_message_patch'])

# ... other code ...