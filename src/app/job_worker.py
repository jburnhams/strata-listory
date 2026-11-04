"""One isolated durable job; the server supervisor owns its deadline."""
import sys
from app.jobs import JobQueue
from app import db,oslib


def main(argv=None):
    args=sys.argv[1:] if argv is None else argv
    if len(args)!=1:raise ValueError('expected one job ID')
    oslib.lower_job_priority()
    from app.server import tasks
    # The supervisor verified/migrated this database before claiming the job.
    # Repeating a full quick_check for every isolated book would scan it 800 times.
    db._checked.add(db.path())
    queue=JobQueue(lambda domain,kind,options:tasks.execute(queue,domain,kind,options))
    queue.execute_claimed(args[0])
    return 0


if __name__=='__main__':sys.exit(main())
