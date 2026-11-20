"""Standalone Pyrogram session string generator (CLI utility, not part of bot runtime)."""
import asyncio


def main() -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
    from pyrogram import Client

    api_id = int(input("API_ID: "))
    api_hash = input("API_HASH: ")

    with Client("gen", api_id=api_id, api_hash=api_hash, in_memory=True) as app:
        print("\nYour session string:\n")
        print(app.export_session_string())


if __name__ == "__main__":
    main()
