CC      = gcc
CFLAGS  = -Wall -O2
LDFLAGS = -lpthread
BIN     = bin

all: $(BIN)/switch $(BIN)/host

# 可选真实蓝牙传输，旧 UDP 构建不增加依赖。Ubuntu: libbluetooth-dev
bluetooth: all $(BIN)/bt-channel

$(BIN)/bt-channel: src/bt_channel.c src/frame.h | $(BIN)
	$(CC) $(CFLAGS) -Wextra -o $@ src/bt_channel.c -lbluetooth

$(BIN):
	mkdir -p $(BIN)

$(BIN)/switch: src/switch.c src/frame.c src/frame.h | $(BIN)
	$(CC) $(CFLAGS) -o $@ src/switch.c src/frame.c $(LDFLAGS)

$(BIN)/host: src/host.c src/frame.c src/frame.h | $(BIN)
	$(CC) $(CFLAGS) -o $@ src/host.c src/frame.c $(LDFLAGS)

clean:
	rm -rf $(BIN)

.PHONY: all bluetooth clean
