CC      = gcc
CFLAGS  = -Wall -O2
LDFLAGS = -lpthread
BIN     = bin

all: $(BIN)/switch $(BIN)/host

# 可选真实蓝牙传输，旧 UDP 构建不增加依赖。Ubuntu: libbluetooth-dev
bluetooth: all $(BIN)/bt-channel

$(BIN)/bt-channel: src/bt_channel.c src/frame.h src/hier_wire.c src/hier.h src/frame.c | $(BIN)
	$(CC) $(CFLAGS) -Wextra -o $@ src/bt_channel.c src/hier_wire.c src/frame.c -lbluetooth

$(BIN):
	mkdir -p $(BIN)

$(BIN)/switch: src/switch.c src/frame.c src/frame.h src/hier_switch.c src/hier_wire.c src/hier.h | $(BIN)
	$(CC) $(CFLAGS) -DWITH_HIERARCHICAL -o $@ src/switch.c src/hier_switch.c src/hier_wire.c src/frame.c $(LDFLAGS)

$(BIN)/host: src/host.c src/frame.c src/frame.h src/hier_host.c src/hier_wire.c src/hier.h | $(BIN)
	$(CC) $(CFLAGS) -DWITH_HIERARCHICAL -o $@ src/host.c src/hier_host.c src/hier_wire.c src/frame.c $(LDFLAGS)

clean:
	rm -rf $(BIN)

.PHONY: all bluetooth clean
