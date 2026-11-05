/* 使用真实switch实现的并发回归：轮转覆盖、查询副本和CLI快照同时运行。
 * 可用 -fsanitize=thread 编译；不引入测试框架或替代实现。 */
#define main switch_program_main
#include "../src/switch.c"
#undef main
#include <assert.h>
#include <sched.h>

static pthread_barrier_t start_barrier;
enum { ROUNDS = 20000, ADDRESS_COUNT = 32 };

static struct sockaddr_in endpoint(uint16_t addr, unsigned generation)
{
    struct sockaddr_in sa = {0};
    sa.sin_family = AF_INET;
    sa.sin_addr.s_addr = htonl(0x7f000000u | ((generation & 255u) << 16) | addr);
    sa.sin_port = htons((uint16_t)(addr ^ ((generation & 255u) << 8)));
    return sa;
}

static void verify_endpoint(uint16_t addr, const struct sockaddr_in *sa)
{
    unsigned ip = ntohl(sa->sin_addr.s_addr);
    assert(sa->sin_family == AF_INET);
    assert((ip & 65535u) == addr);
    assert(ntohs(sa->sin_port) == (uint16_t)(addr ^ (((ip >> 16) & 255u) << 8)));
}

static void *writer(void *arg)
{
    unsigned i;
    (void)arg;
    pthread_barrier_wait(&start_barrier);
    for (i = 0; i < ROUNDS; i++) {
        uint16_t addr = (uint16_t)(0x0a01 + i % ADDRESS_COUNT);
        struct sockaddr_in sa = endpoint(addr, i / ADDRESS_COUNT);
        learn_add(&ports[0], addr, &sa);
        ST_INC(st_rx);
        if (i % 32 == 0) sched_yield();
    }
    return NULL;
}

static void *reader(void *arg)
{
    unsigned i;
    (void)arg;
    pthread_barrier_wait(&start_barrier);
    for (i = 0; i < ROUNDS; i++) {
        struct sockaddr_in sa;
        LearnEntry entries[MAX_LEARN];
        uint16_t addr = (uint16_t)(0x0a01 + i % ADDRESS_COUNT);
        int n, k;
        if (learn_find(&ports[0], addr, &sa)) verify_endpoint(addr, &sa);
        n = learn_snapshot(&ports[0], entries);
        assert(n >= 0 && n <= MAX_LEARN);
        for (k = 0; k < n; k++) verify_endpoint(entries[k].addr, &entries[k].sa);
    }
    return NULL;
}

static void *cli_reader(void *arg)
{
    int i;
    (void)arg;
    pthread_barrier_wait(&start_barrier);
    for (i = 0; i < 100; i++) {
        print_tables();
        reset_stats();
        print_stats();
        sched_yield();
    }
    return NULL;
}

int main(void)
{
    pthread_t threads[4];
    struct sockaddr_in first = endpoint(0x0a01, 1), newer = endpoint(0x0a01, 2), copy;
    LearnEntry snapshot[MAX_LEARN];
    nports = 1; myid = 1;
    ports[0].id = 3; ports[0].is_ext = 1; ports[0].ext_net = 0x0a;
    assert(pthread_mutex_init(&ports[0].learn_lock, NULL) == 0);
    learn_add(&ports[0], 0x0a01, &first);
    assert(learn_find(&ports[0], 0x0a01, &copy));
    learn_add(&ports[0], 0x0a01, &newer);
    assert(memcmp(&copy, &first, sizeof(copy)) == 0); /* 返回副本不会随表项更新而改变 */
    assert(pthread_barrier_init(&start_barrier, NULL, 4) == 0);
    assert(pthread_create(&threads[0], NULL, writer, NULL) == 0);
    assert(pthread_create(&threads[1], NULL, reader, NULL) == 0);
    assert(pthread_create(&threads[2], NULL, reader, NULL) == 0);
    assert(pthread_create(&threads[3], NULL, cli_reader, NULL) == 0);
    for (int i = 0; i < 4; i++) assert(pthread_join(threads[i], NULL) == 0);
    assert(learn_snapshot(&ports[0], snapshot) == MAX_LEARN);
    assert(pthread_mutex_destroy(&ports[0].learn_lock) == 0);
    assert(pthread_barrier_destroy(&start_barrier) == 0);
    printf("PASS learning concurrency: writes=%d readers=2 lookups_and_snapshots=%d cli_snapshots=100\n",
           ROUNDS, ROUNDS * 2);
    return 0;
}
