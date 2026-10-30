CC      = gcc
CFLAGS  = -Wall -O2
LDFLAGS = -lpthread
BIN     = bin

all: $(BIN)/switch $(BIN)/host

$(BIN):
	mkdir -p $(BIN)

$(BIN)/switch: src/switch.c src/frame.c src/frame.h | $(BIN)
	$(CC) $(CFLAGS) -o $@ src/switch.c src/frame.c $(LDFLAGS)

$(BIN)/host: src/host.c src/frame.c src/frame.h | $(BIN)
	$(CC) $(CFLAGS) -o $@ src/host.c src/frame.c $(LDFLAGS)

clean:
	rm -rf $(BIN)

.PHONY: all clean
