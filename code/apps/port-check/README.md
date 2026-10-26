# port-check

Checks which common TCP ports are open on a computer **you own**, and prints
a table. By default it checks this computer (127.0.0.1).

> Only check computers you own or have written permission to test. Checking
> other people's systems can be against the law. For an address outside your
> own network (not loopback or private), the app refuses unless you add
> `--authorised` to confirm you have that permission.

```
$ tb main.tb
Port check for 127.0.0.1 (127.0.0.1), 29 ports

PORT   SERVICE        STATE
21     ftp            closed
22     ssh            closed
...
5432   postgres       OPEN
...
1 open, 28 closed or filtered.
```

## Usage

```
tb main.tb                          # this computer, common ports
tb main.tb 192.168.1.20             # a machine on your own network
tb main.tb 127.0.0.1 22,80,8000-8010
tb main.tb 127.0.0.1 --banners      # also show what open services say first
tb main.tb my-server.example.com --authorised   # a public host you own
```

`PORTS` is a list like `22,80,443`, a range like `8000-8010`, or both
(at most 1024 per run). Each port gets a normal TCP connection with a
1-second timeout; several are tried at once.

## What it teaches

- `net.check_ports(host, ports, timeout)` gives back a map port -> true/false.
- `net.common_ports` maps port numbers to service names (22 -> "ssh").
- `net.resolve`, `net.is_ip` and `net.is_private`: know where your checks
  really go before you send them.
- `net.banner` reads the greeting a service sends (SSH, FTP and mail servers
  announce their software and version), which is how you spot an outdated
  service on your own machines.
- Reading command-line arguments with `os.args`.

It is an audit tool for your own machines: an open port you don't recognise
means a program is listening that you should know about. It only makes
ordinary connections; it doesn't scan stealthily or try to break in.
