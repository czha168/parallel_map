# Copyright (c) 2019 MIT Probabilistic Computing Project.
# See LICENSE.txt

import pickle
import os
import struct
import traceback

from multiprocess import Pipe
from multiprocess import Process
from multiprocess import cpu_count


def le32enc(n):
    return struct.pack('<I', n)

def le32dec(s):
    return struct.unpack('<I', s)[0]


# Not using multiprocessing.pool because it is not clear how to get it
# to share data from the parent to the child process.
def parallel_map(f, l, parallelism=None):

    ncpu = cpu_count() if parallelism is None else parallelism

    # Per-process action: grab an input from the input queue, compute,
    # toss the output in the output queue.
    def process_input(childno, inq_rd, outq_wr, retq_wr):
        while True:
            i = inq_rd.recv()
            if i is None:
                break
            x = l[i]
            try:
                ok, fx = True, f(x)
            except Exception as _e:
                ok, fx = False, traceback.format_exc()
            os.write(retq_wr, le32enc(childno))
            try:
                outq_wr.send((i, ok, fx))
            except pickle.PicklingError:
                outq_wr.send((i, False, traceback.format_exc()))

    def process_output(fl, ctr, output):
        (i, ok, fx) = output
        if not ok:
            raise RuntimeError('Subprocess %d failed: %s' % (i, fx,))
        fl[i] = fx
        ctr[0] -= 1

    # Create the queues and worker processes.
    retq_rd, retq_wr = os.pipe()
    inq = [Pipe(duplex=False) for _ in range(ncpu)]
    outq = [Pipe(duplex=False) for _ in range(ncpu)]
    process = [
        Process(target=process_input, args=(j, inq[j][0], outq[j][1], retq_wr))
        for j in range(ncpu)
    ]

    # Prepare to bail by terminating all the worker processes.
    try:

        # Start the worker processes.
        for p in process:
            p.start()

        # Queue up the tasks one by one.  If the input queue is full,
        # process an output item to free up a worker process and try
        # again.
        n = len(l)
        fl = [None] * n
        ctr = [n]
        iterator = iter(range(n))
        for j, i in zip(range(ncpu), iterator):
            inq[j][1].send(i)
        for i in iterator:
            j = le32dec(os.read(retq_rd, 4))
            process_output(fl, ctr, outq[j][0].recv())
            inq[j][1].send(i)

        # Process all the remaining output items.
        while 0 < ctr[0]:
            j = le32dec(os.read(retq_rd, 4))
            process_output(fl, ctr, outq[j][0].recv())

        # Cancel all the worker processes.
        for _inq_rd, inq_wr in inq:
            inq_wr.send(None)

        # Wait for all the worker processes to complete.
        for p in process:
            p.join()

    except Exception as _e:           # paranoia
        # Terminate all subprocesses immediately and reraise.
        for p in process:
            if p.is_alive():
                p.terminate()
        raise

    finally:
        os.close(retq_rd)
        os.close(retq_wr)
        for inq_rd, inq_wr in inq:
            inq_rd.close()
            inq_wr.close()
        for outq_rd, outq_wr in outq:
            outq_rd.close()
            outq_wr.close()

    return fl
