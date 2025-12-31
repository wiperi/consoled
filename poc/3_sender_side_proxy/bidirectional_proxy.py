#!/usr/bin/env python3
"""
Bidirectional Proxy between /dev/C0-1 and /dev/ttyV0

Rules:
1. Read from /dev/C0-1:
   - If "hello" is received, discard it
   - All other bytes are forwarded to /dev/ttyV0

2. Read from /dev/ttyV0:
   - All data is forwarded to /dev/C0-1
"""

import asyncio
import os
import sys

# Device paths
DEV_C0_1 = "/dev/C0-1"
DEV_TTYV0 = "/dev/ttyV0"

# Filter pattern
FILTER_PATTERN = b"hello"


async def forward_c0_to_ttyv0(reader_c0, writer_ttyv0):
    """
    Read from /dev/C0-1 and forward to /dev/ttyV0.
    Discard any "hello" messages.
    """
    buffer = b""
    
    while True:
        try:
            data = await reader_c0.read(1024)
            if not data:
                print("[C0-1 -> ttyV0] Connection closed")
                break
            
            # Print each byte received
            print(f"[C0-1] Received {len(data)} bytes:")
            for i, byte in enumerate(data):
                print(f"  [{i}] 0x{byte:02X} ({byte:3d}) {repr(chr(byte)) if 32 <= byte < 127 else '.'}") 
            
            # Add to buffer for pattern matching
            buffer += data
            
            # Process buffer, looking for "hello" pattern
            while buffer:
                # Check if buffer starts with "hello"
                if buffer.startswith(FILTER_PATTERN):
                    print(f"[C0-1 -> ttyV0] Discarding 'hello'")
                    buffer = buffer[len(FILTER_PATTERN):]
                    continue
                
                # Check if we might have a partial "hello" at the end
                potential_match = False
                for i in range(1, len(FILTER_PATTERN)):
                    if buffer.endswith(FILTER_PATTERN[:i]):
                        # Keep the potential partial match in buffer
                        to_send = buffer[:-i]
                        buffer = buffer[-i:]
                        potential_match = True
                        if to_send:
                            print(f"[C0-1 -> ttyV0] Forwarding: {to_send!r}")
                            writer_ttyv0.write(to_send)
                            await writer_ttyv0.drain()
                        break
                
                if not potential_match:
                    # No pattern match, forward all data
                    print(f"[C0-1 -> ttyV0] Forwarding: {buffer!r}")
                    writer_ttyv0.write(buffer)
                    await writer_ttyv0.drain()
                    buffer = b""
                    
        except Exception as e:
            print(f"[C0-1 -> ttyV0] Error: {e}")
            break


async def forward_ttyv0_to_c0(reader_ttyv0, writer_c0):
    """
    Read from /dev/ttyV0 and forward all data to /dev/C0-1.
    """
    while True:
        try:
            data = await reader_ttyv0.read(1024)
            if not data:
                print("[ttyV0 -> C0-1] Connection closed")
                break
            
            # Print each byte received
            print(f"[ttyV0] Received {len(data)} bytes:")
            for i, byte in enumerate(data):
                print(f"  [{i}] 0x{byte:02X} ({byte:3d}) {repr(chr(byte)) if 32 <= byte < 127 else '.'}")
            
            print(f"[ttyV0 -> C0-1] Forwarding: {data!r}")
            writer_c0.write(data)
            await writer_c0.drain()
            
        except Exception as e:
            print(f"[ttyV0 -> C0-1] Error: {e}")
            break


async def open_device(path):
    """Open a device file and return asyncio reader/writer."""
    # Open device in read-write mode
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    
    # Create read transport
    read_transport, _ = await loop.connect_read_pipe(lambda: protocol, os.fdopen(fd, 'rb', buffering=0))
    
    # Create write transport
    fd_write = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    write_transport, write_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, 
        os.fdopen(fd_write, 'wb', buffering=0)
    )
    writer = asyncio.StreamWriter(write_transport, write_protocol, reader, loop)
    
    return reader, writer


async def main():
    print(f"Starting bidirectional proxy...")
    print(f"  {DEV_C0_1} <-> {DEV_TTYV0}")
    print(f"  Filter: 'hello' messages from {DEV_C0_1} will be discarded")
    print()
    
    # Check if devices exist
    for dev in [DEV_C0_1, DEV_TTYV0]:
        if not os.path.exists(dev):
            print(f"Error: Device {dev} does not exist")
            sys.exit(1)
    
    try:
        # Open devices
        reader_c0, writer_c0 = await open_device(DEV_C0_1)
        reader_ttyv0, writer_ttyv0 = await open_device(DEV_TTYV0)
        
        print("Devices opened successfully. Starting proxy...")
        
        # Run both forwarding tasks concurrently
        await asyncio.gather(
            forward_c0_to_ttyv0(reader_c0, writer_ttyv0),
            forward_ttyv0_to_c0(reader_ttyv0, writer_c0)
        )
        
    except PermissionError as e:
        print(f"Permission denied: {e}")
        print("Try running with sudo or add user to appropriate group")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProxy stopped by user")
