import time

class BotDIn:
    def __init__(self):
        self.session_max_minutes = 120  # Default session max minutes
        self.idle_stop_minutes = 20       # Default idle stop minutes
        self.start_time = time.time()
        self.last_activity_time = time.time()

    def connect(self):
        # Connect to the IQ Option without threads, with small sleep and change_balance 
        print("Connecting to IQ Option...")
        # Implement the connection logic according to BOTDIN8VELAS
        time.sleep(1)  # Replace with actual connection code

    def execute_order(self):
        # Order execution logic that returns accepted boolean
        accepted = True  # Logic to determine if order was accepted
        self.reset_idle_timer()
        return accepted

    def reset_idle_timer(self):
        self.last_activity_time = time.time()

    def remaining_time(self):
        elapsed_time = time.time() - self.start_time
        remaining = (self.session_max_minutes * 60) - elapsed_time
        return max(0, remaining)

    def enforce_stop_conditions(self):
        idle_time = (time.time() - self.last_activity_time) / 60
        if self.remaining_time() <= 0 or idle_time >= self.idle_stop_minutes:
            print("Session ended due to time limits.")
            return True
        return False

    def run(self):
        self.connect()
        while True:
            if self.enforce_stop_conditions():
                break
            print(f"Remaining time: {self.remaining_time()} seconds")
            # Here would be the logic to handle candle events, etc.
            time.sleep(1)  # Main loop delay

# Instantiate and run the bot
bot = BotDIn()
bot.run()