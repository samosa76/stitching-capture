import os

from pyModbusTCP.client import ModbusClient
from dotenv import load_dotenv

load_dotenv()

#--- Adam Connection Settings ---#
ADAM_IP = os.getenv("ADAM_IP", "192.168.18.212")
ADAM_PORT = int(os.getenv("ADAM_PORT", "502"))

DO2_ADDRESS = int(os.getenv("DO2_ADDRESS", "0"))
DO3_ADDRESS = int(os.getenv("DO3_ADDRESS", "0"))

client = ModbusClient(host=ADAM_IP, port=ADAM_PORT, auto_open=True, auto_close=False)

def read_coil(address: int) -> bool:
    result = client.read_coils(address, 1)

    if result is None:
        raise ConnectionError(f"Failed to read coil {address} from ADAM")
    return result[0]


def read_do2() -> bool:
    return read_coil(DO2_ADDRESS)

def read_do3() -> bool:
    return read_coil(DO3_ADDRESS)