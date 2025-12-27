#!/bin/bash
sudo socat -d -d pty,raw,echo=0,link=/dev/s1 pty,raw,echo=0,link=/dev/r1 &
sudo socat -d -d pty,raw,echo=0,link=/dev/s2 pty,raw,echo=0,link=/dev/r2 &
sudo socat -d -d pty,raw,echo=0,link=/dev/s3 pty,raw,echo=0,link=/dev/r3 &