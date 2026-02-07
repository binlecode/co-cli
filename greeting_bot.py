#!/usr/bin/env python3
import argparse
import random
import time
from datetime import datetime
from typing import List

# Configuration
GREETING_DELAY = 2  # seconds between greetings
ROUNDS = 3
TIME_CONFIG = {
    "morning": (5, 12),
    "afternoon": (12, 18),
    "evening": (18, 22),
    "night": (22, 6),  # wraps around midnight
}

TIME_GREETINGS = {
    "morning": "Good morning",
    "afternoon": "Good afternoon", 
    "evening": "Good evening",
    "night": "Good night",
}

def get_greeting_time() -> str:
    """Return time of day based on current hour."""
    hour = datetime.now().hour
    for period, (start, end) in TIME_CONFIG.items():
        if start < end:
            if start <= hour < end:
                return period
        else:  # wraps around midnight
            if hour >= start or hour < end:
                return period
    return "morning"  # fallback

def get_greeting(custom_greeting: str = None) -> str:
    """Return a greeting message."""
    time_period = get_greeting_time()
    time_greeting = TIME_GREETINGS[time_period]
    
    if custom_greeting:
        return f"{time_greeting}! {custom_greeting}"
    
    greetings = [
        f"{time_greeting}! How can I help you today?",
        f"{time_greeting}! Hope you're having a great day.",
        f"{time_greeting}! Wishing you a wonderful day ahead.",
        f"{time_greeting}! Ready to tackle your tasks?",
        f"{time_greeting}! Remember to take breaks and stay hydrated.",
    ]
    
    return random.choice(greetings)

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Print time-based greetings at regular intervals."
    )
    parser.add_argument(
        "-r", "--rounds", 
        type=int, 
        default=ROUNDS,
        help=f"Number of greeting rounds (default: {ROUNDS})"
    )
    parser.add_argument(
        "-d", "--delay", 
        type=float, 
        default=GREETING_DELAY,
        help=f"Greeting delay in seconds (default: {GREETING_DELAY})"
    )
    parser.add_argument(
        "-g", "--greeting",
        type=str,
        help="Custom greeting message to use"
    )
    return parser.parse_args()

def main():
    args = parse_args()
    
    print(f"👋 Greeting bot started! Printing greetings every {args.delay} seconds for {args.rounds} rounds.\n")
    
    for round_num in range(args.rounds):
        greeting = get_greeting(args.greeting)
        print(f"Round {round_num + 1}: {greeting}")
        if round_num < args.rounds - 1:
            time.sleep(args.delay)
    
    print("\n👋 Greeting bot stopped!")

if __name__ == "__main__":
    main()
