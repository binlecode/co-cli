import sys
def __getattr__(name):
    print(f"__getattr__ called for {name}")
    return name
