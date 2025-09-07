import asyncio
import time

async def main():
    while True:
        print("✅ Worker is running...")
        await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
