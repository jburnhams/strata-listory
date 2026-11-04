"""OS helpers (app.oslib): file lock, process liveness, termination, priority, CLI command."""
import os, subprocess, sys, time, types
import pytest
from app import oslib


@pytest.mark.parametrize('platform,available',[('linux',True),('linux',False),('darwin',True),('win32',True)])
def test_job_priority_lowers_cpu_and_uses_idle_linux_io_when_available(monkeypatch,platform,available):
    calls=[];monkeypatch.setattr(oslib.sys,'platform',platform)
    monkeypatch.setattr(oslib,'lower_priority',lambda nice:calls.append(('cpu',nice)))
    monkeypatch.setattr(oslib.shutil,'which',lambda name:'/bin/ionice' if available else None)
    monkeypatch.setattr(subprocess,'run',lambda command,**kw:calls.append((command,kw)))
    oslib.lower_job_priority()
    assert calls[0]==('cpu',19)
    if platform=='linux' and available:
        assert calls[1][0]==['/bin/ionice','-c','3','-p',str(os.getpid())]
        assert calls[1][1]['timeout']==5 and calls[1][1]['stdout']==subprocess.DEVNULL
    else:assert len(calls)==1


@pytest.mark.parametrize('error',[OSError('unsupported'),subprocess.TimeoutExpired('ionice',5)])
def test_unavailable_io_priority_does_not_fail_a_job(monkeypatch,error):
    monkeypatch.setattr(oslib.sys,'platform','linux');monkeypatch.setattr(oslib,'lower_priority',lambda nice:None)
    monkeypatch.setattr(oslib.shutil,'which',lambda name:'/bin/ionice')
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:(_ for _ in ()).throw(error))
    oslib.lower_job_priority()


@pytest.mark.parametrize('windows',[False,True])
def test_job_process_is_detached_and_does_not_buffer_output(monkeypatch,windows):
    calls=[];process=object();monkeypatch.setattr(oslib,'WIN',windows)
    monkeypatch.setattr(subprocess,'Popen',lambda command,**kw:calls.append((command,kw)) or process)
    assert oslib.job_process(['worker'],dict(APP_DATA='/saved')) is process
    options=calls[0][1]
    assert options['stdout']==options['stderr']==options['stdin']==subprocess.DEVNULL
    assert options.get('start_new_session',False)==(not windows)
    assert options['env']==dict(APP_DATA='/saved')


def test_start_job_pins_database_and_uses_the_current_python(monkeypatch):
    calls=[]
    monkeypatch.setattr(oslib,'cli_command',lambda:['python','-m','app'])
    monkeypatch.setattr(oslib,'job_process',lambda *args,**kwargs:calls.append((*args,kwargs)) or 'child')
    assert oslib.start_job('saved','/database')=='child'
    assert calls[0][0]==['python','-m','app.job_worker','saved'] and calls[0][1]['APP_DATA']=='/database'
    assert calls[0][1]['APP_JOB_SUPERVISED']=='1' and calls[0][2]==dict(supervised=True)


def test_launching_eight_hundred_jobs_does_not_grow_the_python_environment(monkeypatch):
    monkeypatch.setenv('PYTHONPATH','extra');monkeypatch.setenv('PYTHONWARNINGS','default');paths=[]
    monkeypatch.setattr(oslib,'job_process',lambda command,env,**kw:paths.append(env['PYTHONPATH']))
    for n in range(800):oslib.start_job(str(n),'/database')
    assert set(paths)=={os.path.join(oslib.ROOT_DIR,'src')+os.pathsep+'extra'}


@pytest.mark.parametrize('windows',[False,True])
def test_job_termination_kills_descendants_and_reaps_the_child(monkeypatch,windows):
    import signal
    monkeypatch.setattr(signal,'SIGKILL',9,raising=False)
    calls=[];monkeypatch.setattr(oslib,'WIN',windows)
    monkeypatch.setattr(oslib,'kill_tree',lambda pid:calls.append(('tree',pid)))
    monkeypatch.setattr(os,'killpg',lambda pid,sig:calls.append(('group',pid)),raising=False)
    def wait(timeout):
        calls.append(('wait',timeout));raise subprocess.TimeoutExpired('child',timeout)
    process=types.SimpleNamespace(pid=123,wait=wait)
    oslib.kill_job(process)
    assert calls==[('tree' if windows else 'group',123),('wait',5)]


def test_file_lock_excludes_other_processes(tmp_path):
    p = tmp_path / 'lock'
    code = ("import sys, time; from app import oslib\n"
            "with open(sys.argv[1], 'a+') as f, oslib.file_lock(f):\n    print('locked', flush=True); time.sleep(0.5)\n")
    child = subprocess.Popen([sys.executable, '-c', code, str(p)], stdout=subprocess.PIPE, text=True, env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
    assert child.stdout.readline().strip() == 'locked'
    t0 = time.time()
    with open(p, 'a+') as f, oslib.file_lock(f): waited = time.time() - t0
    child.wait()
    assert waited > 0.2, waited


def test_file_lock_is_released_after_the_block(tmp_path):
    with open(tmp_path / 'l', 'a+') as f:
        for _ in range(3):
            with oslib.file_lock(f): pass


def test_file_lock_windows_branch(tmp_path, monkeypatch):
    calls = []
    fake_msvcrt = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0)
    def locking(fd, mode, n):
        calls.append(mode)
        if len(calls) == 1: raise OSError('busy')
    fake_msvcrt.locking = locking
    monkeypatch.setattr(oslib, 'WIN', True)
    monkeypatch.setitem(sys.modules, 'msvcrt', fake_msvcrt)
    monkeypatch.setattr(time, 'sleep', lambda _: None)
    with open(tmp_path / 'wlock', 'a+') as f, oslib.file_lock(f):
        pass
    assert calls == [2, 2, 0]

    flock_calls = []
    fake_fcntl = types.SimpleNamespace(LOCK_EX=1, LOCK_UN=8, flock=lambda f, op: flock_calls.append(op))
    monkeypatch.setattr(oslib, 'WIN', False)
    monkeypatch.setitem(sys.modules, 'fcntl', fake_fcntl)
    with open(tmp_path / 'plock', 'a+') as f, oslib.file_lock(f):
        pass
    assert flock_calls == [1, 8]


def test_pid_alive(monkeypatch):
    assert oslib.pid_alive(os.getpid()) and oslib.pid_alive(str(os.getpid()))
    assert not oslib.pid_alive(None) and not oslib.pid_alive('x') and not oslib.pid_alive(0)
    p = subprocess.Popen([sys.executable, '-c', 'pass']); p.wait()
    assert not oslib.pid_alive(p.pid)

    monkeypatch.setattr(oslib, 'WIN', False)
    monkeypatch.setattr(os, 'kill', lambda pid, sig: None)
    assert oslib.pid_alive(1234)
    monkeypatch.setattr(os, 'kill', lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))
    assert not oslib.pid_alive(1234)
    monkeypatch.setattr(os, 'kill', lambda pid, sig: (_ for _ in ()).throw(PermissionError()))
    assert oslib.pid_alive(1234)
    monkeypatch.setattr(os, 'kill', lambda pid, sig: (_ for _ in ()).throw(OSError('other')))
    assert not oslib.pid_alive(1234)


def test_pid_alive_windows_branch(monkeypatch):
    import ctypes
    monkeypatch.setattr(oslib, 'WIN', True)
    closed = []
    class FakeKernel32:
        def OpenProcess(self, access, inherit, pid): return 0 if pid == 99 else 42
        def GetExitCodeProcess(self, h, ptr):
            ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ulong))[0] = 259
            return 1
        def CloseHandle(self, h): closed.append(h)
    monkeypatch.setattr(ctypes, 'windll', types.SimpleNamespace(kernel32=FakeKernel32()), raising=False)
    assert not oslib.pid_alive(99)
    assert oslib.pid_alive(100) and closed == [42]


def test_kill_tree_ends_the_process(monkeypatch):
    p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    assert oslib.pid_alive(p.pid)
    oslib.kill_tree(p.pid)
    p.wait(timeout=10)
    assert not oslib.pid_alive(p.pid)

    # Child process recursion and OSError fallback when pgrep is absent
    monkeypatch.setattr(oslib, 'WIN', False)
    killed = []
    monkeypatch.setattr(os, 'kill', lambda pid, sig: killed.append(pid))
    calls = [types.SimpleNamespace(stdout='222\n'), types.SimpleNamespace(stdout='')]
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: calls.pop(0) if calls else (_ for _ in ()).throw(OSError('no pgrep')))
    oslib.kill_tree(111)
    oslib.kill_tree(333)
    assert killed == [222, 111, 333]

    # Windows branch
    win_cmds = []
    monkeypatch.setattr(oslib, 'WIN', True)
    monkeypatch.setattr(subprocess, 'run', lambda cmd, **kw: win_cmds.append(cmd))
    oslib.kill_tree(123)
    assert win_cmds == [['taskkill', '/T', '/F', '/PID', '123']]


def test_lower_priority_and_venv_python(monkeypatch):
    monkeypatch.setattr(oslib, 'WIN', False)
    nices = []
    monkeypatch.setattr(os, 'getpriority', lambda *a: -10, raising=False)
    monkeypatch.setattr(os, 'PRIO_PROCESS', 0, raising=False)
    monkeypatch.setattr(os, 'setpriority', lambda which,pid,n: nices.append(n), raising=False)
    oslib.lower_priority(10)
    assert nices == [10]
    assert oslib.venv_python('/tmp/v').endswith('bin/python') or oslib.venv_python('/tmp/v').endswith('bin\\python')

    import ctypes
    prio = []
    class FakeK32:
        def GetCurrentProcess(self): return 1
        def SetPriorityClass(self, h, cls): prio.append(cls)
    monkeypatch.setattr(oslib, 'WIN', True)
    monkeypatch.setattr(ctypes, 'windll', types.SimpleNamespace(kernel32=FakeK32()), raising=False)
    oslib.lower_priority(19)
    oslib.lower_priority(5)
    assert prio == [0x40, 0x4000]
    assert oslib.venv_python('C:\\v').endswith('Scripts/python.exe') or oslib.venv_python('C:\\v').endswith('Scripts\\python.exe')


def test_cli_command_runs_the_cli():
    out = subprocess.run([*oslib.cli_command(), '--help'], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and 'usage' in out.stdout, out.stderr[-300:]


@pytest.mark.parametrize('windows',[False,True])
def test_supervised_process_uses_a_private_pipe(monkeypatch,windows):
    calls=[];monkeypatch.setattr(oslib,'WIN',windows)
    monkeypatch.setattr(subprocess,'Popen',lambda command,**kw:calls.append(kw))
    oslib.job_process(['worker'],supervised=True)
    assert calls[0]['stdin']==subprocess.PIPE and calls[0]['stdout']==subprocess.DEVNULL


def test_supervisor_watch_waits_for_pipe_eof_before_terminating(monkeypatch):
    monkeypatch.delenv('APP_JOB_SUPERVISED',raising=False);calls=[];threads=[]
    monkeypatch.setattr(oslib.threading,'Thread',lambda **kw:types.SimpleNamespace(start=lambda:threads.append(kw)))
    oslib.watch_supervisor();assert threads==[]
    monkeypatch.setenv('APP_JOB_SUPERVISED','1');oslib.watch_supervisor()
    assert threads[0]['daemon'] is True
    reads=iter([b'x',b'']);monkeypatch.setattr(os,'read',lambda fd,n:next(reads))
    monkeypatch.setattr(oslib,'terminate_job_group',lambda:calls.append('terminated'))
    threads[0]['target']();assert calls==['terminated']


@pytest.mark.parametrize('windows,group',[(True,123),(False,123),(False,999)])
def test_orphan_termination_only_kills_its_own_session(monkeypatch,windows,group):
    import signal
    monkeypatch.setattr(signal,'SIGKILL',9,raising=False)
    monkeypatch.setattr(oslib,'WIN',windows);calls=[]
    monkeypatch.setattr(os,'getpid',lambda:123)
    monkeypatch.setattr(os,'getpgrp',lambda:group,raising=False)
    monkeypatch.setattr(os,'killpg',lambda pid,sig:calls.append(('group',pid)),raising=False)
    monkeypatch.setattr(oslib,'kill_tree',lambda pid:calls.append(('tree',pid)))
    monkeypatch.setattr(os,'_exit',lambda code:calls.append(('exit',code)))
    oslib.terminate_job_group()
    assert calls==([('tree',123)] if windows else [('group',123)] if group==123 else [])+[('exit',1)]


def test_lower_priority_does_not_raise_an_already_lower_priority(monkeypatch):
    monkeypatch.setattr(oslib,'WIN',False);values=[]
    monkeypatch.setattr(os,'PRIO_PROCESS',0,raising=False)
    monkeypatch.setattr(os,'getpriority',lambda *a:20,raising=False)
    monkeypatch.setattr(os,'setpriority',lambda which,pid,value:values.append(value),raising=False)
    oslib.lower_priority(19)
    assert values==[20]
