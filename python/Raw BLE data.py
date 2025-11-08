# pip install bleak
import asyncio
from bleak import BleakClient

ADDRESS = "XX:XX:XX:XX:XX:XX"  # e.g. "c4:a9:b8:3a:5d:bd"
# Most AN9002-style meters notify on FFF4. If your platform prefers handles, you can use an int handle instead.
CHAR = "0000fff4-0000-1000-8000-00805f9b34fb"  # or CHAR = 8

# Vendor XOR key (cycles if payload longer than key)
XOR_KEY = bytes([0x41,0x21,0x73,0x55,0xA2,0xC1,0x32,0x71,0x66,0xAA,0x3B,0xD0,0xE2,0xA8,0x33,0x14,0x20,0x21,0xAA,0xBB])

def hexdump(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)

def deobfuscate(pkt: bytes) -> bytes:
    # XOR each byte with the repeating key
    return bytes(b ^ XOR_KEY[i % len(XOR_KEY)] for i, b in enumerate(pkt))

def bit_reverse_byte(x: int) -> int:
    # Reverse bit order within a byte: b7..b0 -> b0..b7
    x = ((x & 0xF0) >> 4) | ((x & 0x0F) << 4)
    x = ((x & 0xCC) >> 2) | ((x & 0x33) << 2)
    x = ((x & 0xAA) >> 1) | ((x & 0x55) << 1)
    return x

def bit_reverse_blob(b: bytes) -> bytes:
    return bytes(bit_reverse_byte(x) for x in b)

def on_notify(_handle, data: bytes):
    # 1) raw BLE notification bytes
    raw_hex = hexdump(data)

    # 2) de-XORed "plain" bytes (should match your UART capture)
    plain = deobfuscate(data)
    plain_hex = hexdump(plain)

    # 3) (optional) bit-reversed view after XOR, if you want to match 7-seg decoding
    # rev_hex = hexdump(bit_reverse_blob(plain))

    print(f"RAW  : {raw_hex}")
    print(f"XOR  : {plain_hex}")
    # print(f"XOR^R: {rev_hex}")  # uncomment if you want bit-reversed too
    print("-")

async def main():
    async with BleakClient(ADDRESS) as client:
        try:
            await client.exchange_mtu(185)  # optional
        except Exception:
            pass

        await client.start_notify(CHAR, on_notify)
        # Keep running; Ctrl+C to stop
        await asyncio.sleep(999999)

if __name__ == "__main__":
    asyncio.run(main())
