import os
import json
import time

def inject_mock_wallet():
    # Inject a test wallet for the polling thread to pick up
    try:
        if os.path.exists("wallets.json"):
            with open("wallets.json", "r") as f:
                data = json.load(f)
        else:
            data = {}
            
        test_chat_id = "123456789" # FAKE ID so it prints locally instead of sending
        test_address = "0x16bf84af3f85f8c8a97597bf2be549dfe0dee637" # Hyperdash #1 Trader
        
        if test_chat_id not in data:
            data[test_chat_id] = {}
        data[test_chat_id]["MockWhale"] = test_address
        
        with open("wallets.json", "w") as f:
            json.dump(data, f)
            
        print("Mock wallet injected. Testing the poll_positions function over 3 cycles...")
    except Exception as e:
        print(f"Error injecting mock wallet: {e}")

if __name__ == "__main__":
    inject_mock_wallet()
