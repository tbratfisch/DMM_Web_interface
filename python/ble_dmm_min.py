"""Minimal BLE DMM client

Connects to a single target device and prints decoded readings to the terminal.
Configure TARGET_NAME or TARGET_ADDR_STR below.
Requires: bleak
"""
import asyncio
import time
from datetime import datetime
import logging

from bleak import BleakClient

# --- Configuration: change these to your device ---
TARGET_NAME = "Bluetooth DMM"
TARGET_ADDR_STR = "XX:XX:XX:XX:XX:XX"  # e.g. "c4:a9:b8:3a:5d:bd"
# ------------------------------------------------- 

LOG = logging.getLogger("ble_dmm_min")


def pre_process(value):
    # value: hex string (no 0x), e.g. '1b8470...'
    hex_data = []
    for i in range(0, len(value), 2):
        hex_data.append(int("0x" + value[i] + value[i+1], base=16))

    def hex_to_binary(x):
        return bin(int(x, 16)).lstrip('0b').zfill(8)

    def LSB_TO_MSB(x):
        return x[::-1]

    xorkey = [0x41,0x21,0x73,0x55,0xa2,0xc1,0x32,0x71,0x66,0xaa,0x3b,0xd0,0xe2,0xa8,0x33,0x14,0x20,0x21,0xaa,0xbb]
    binary_array = []
    fullbinary = ""
    for x in range(len(hex_data)):
        tohex = hex(hex_data[x] ^ xorkey[x])
        tobinary = hex_to_binary(tohex)
        flipped = LSB_TO_MSB(tobinary)
        binary_array.append(flipped)
        fullbinary += flipped
    return fullbinary


class type_detecter:
    type_dict = {
        '11000000':'1',
        '01000000':'2',
        '10000000':'3',
        '00100000':'4',
    }

    @classmethod
    def decode(cls, origin_value):
        return pre_process(origin_value)

    @classmethod
    def type(cls, origin_value):
        type_code = ''
        for i in range(16,24,1):
            type_code = type_code + cls.decode(origin_value)[i]
        return cls.type_dict.get(type_code)


class BaseDecoder:
    digit_dict = {
        '1110111':'0','0010010':'1','1011101':'2','1011011':'3','0111010':'4',
        '1101011':'5','1101111':'6','1010010':'7','1111111':'8','1111011':'9',
        '1111110':'A','0000111':'u','0101101':'t','0001111':'o','0100101':'L',
        '1101101':'E','1101100':'F','0001000':'-'
    }

    @classmethod
    def digit(cls, segment, digi):
        signal = segment[3]+segment[2]+segment[7]+segment[6]+segment[1]+segment[5]+segment[4]
        try:
            if digi is not None:
                digi = digi + cls.digit_dict.get(signal, '')
        except Exception:
            digi = digi + ''
        return digi


class decoder_1(BaseDecoder):
    @classmethod
    def decode(cls, origin_value):
        return pre_process(origin_value)

    @classmethod
    def printdigit(cls, prepared):
        digi = ''
        if prepared[28]=='1':
            digi = digi + '-'
        digi = cls.digit(prepared[28:36], digi)
        if prepared[36]=='1':
            digi = digi + '.'
        digi = cls.digit(prepared[36:44], digi)
        if prepared[44]=='1':
            digi = digi + '.'
        digi = cls.digit(prepared[44:52], digi)
        if prepared[52]=='1':
            digi = digi + '.'
        digi = cls.digit(prepared[52:60], digi)
        if digi == None or digi == '':
            digi = '0'
        return digi

    @classmethod
    def printchar(cls, prepared):
        char_function = []
        char_unit = []
        bits_1 = ["∆", "", "BUZ"]
        for i in range(25,28,1):
            if prepared[i]=='1':
                char_function.append(bits_1[i-25])
        bits_2 = ["HOLD","°F","°C","->","MAX","MIN","%","AC",
                  "F","μ","?5","n","Hz","Ω","K","M",
                  "V","m","DC","A","Auto","?7","μ","m",
                  "?8","?9","?10","?11"]
        function = {60,63,64,65,80}
        for i in range(59+len(bits_2),59,-1):
            if i in function:
                if prepared[i]=='1':
                    char_function.append(bits_2[i-60])
            else:
                if prepared[i]=='1':
                    char_unit.append(bits_2[i-60])
        return [char_function, char_unit]


class decoder_2(decoder_1):
    @classmethod
    def printchar(cls, prepared):
        char_function = []
        char_unit = []
        bits_1 = ["HOLD", "Flash", "BUZ"]
        for i in range(25,28,1):
            if prepared[i]=='1':
                char_function.append(bits_1[i-25])
        bits_2 = ["n", "V", "DC", "AC","F", "->","A", "µ",
            "Ω", "k", "m", "M","", "Hz", "°F", "°C"]
        function = {64,69}
        for i in range(63+len(bits_2),63,-1):
            if i in function:
                if prepared[i]=='1':
                    char_function.append(bits_2[i-64])
            else:
                if prepared[i]=='1':
                    char_unit.append(bits_2[i-64])
        return [char_function, char_unit]


class decoder_3(decoder_1):
    pass


class decoder_4(decoder_1):
    pass


async def read_loop(address: str):
    async with BleakClient(address) as client:
        print(f"Connected: {client.is_connected}")
        # try to detect type
        try:
            raw = bytes(await client.read_gatt_char(8, use_cached=1)).hex()
        except Exception as e:
            print("Failed to read initial char:", e)
            return
        dev_type = type_detecter.type(raw)
        print("Detected type:", dev_type)
        dec = None
        if dev_type == '1':
            dec = decoder_1
        elif dev_type == '2':
            dec = decoder_2
        elif dev_type == '3':
            dec = decoder_3
        elif dev_type == '4':
            dec = decoder_4
        else:
            print("Unknown device type. Will still try with decoder_1.")
            dec = decoder_1

        try:
            while True:
                try:
                    raw = bytes(await client.read_gatt_char(8, use_cached=1)).hex()
                except Exception as e:
                    print("Read failed:", e)
                    break

                try:
                    digi = dec.printdigit(dec.decode(raw))
                    char = dec.printchar(dec.decode(raw))
                    func = ' '.join(char[0])
                    unit = ' '.join(char[1])
                    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
                    print(f"{ts}  {digi} {unit}  {func}")
                except Exception as e:
                    print("Decode error:", e)

                await asyncio.sleep(1/3)
        finally:
            print("Disconnecting")


def main():
    address = TARGET_ADDR_STR
    print(f"Target name: {TARGET_NAME}")
    print(f"Target address: {address}")
    try:
        asyncio.run(read_loop(address))
    except KeyboardInterrupt:
        print("Interrupted by user")


if __name__ == '__main__':
    main()
