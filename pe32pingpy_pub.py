#!/usr/bin/env python3
import asyncio
import decimal
import logging
import os
import subprocess
import sys
import time

import requests

from asyncio_mqtt import Client as MqttClient, MqttError

__version__ = 'p32pingpy_pub-FIXME'

log = logging.getLogger()


class Pe32PingPublisher:
    def __init__(self):
        self._mqtt_broker = os.environ.get(
            'PE32PING_BROKER', 'test.mosquitto.org')
        self._mqtt_topic = os.environ.get(
            'PE32PING_TOPIC', 'myhome/infra/internet/xwwwform')
        self._mqttc = None
        self._guid = os.environ.get(
            'PE32PING_GUID', 'EUI48:11:22:33:44:55:66')

    def open(self):
        self._mqttc = MqttClient(self._mqtt_broker)
        return self._mqttc

    def publish(self, ping_results):
        # FIXME:
        # 2022-01-24 23:10:58 Task exception was never retrieved
        # future: <Task finished name='Task-4'
        #   coro=<IskraMe162ValueProcessor.publish() done,
        #   defined at ./iec62056_test_client.py:148> exception=ValueError()>
        # Traceback (most recent call last):
        #   File "./iec62056_test_client.py", line 152, in publish
        #     raise ValueError('x')  # should be caught somewhere!!!
        # ValueError: x
        # FIXME: retrieve task here on next publish?? that should get us
        # a nice exception then...
        asyncio.create_task(self._publish(ping_results))

    async def _publish(self, ping_results):
        log.debug(f'_publish: {ping_results}')

        values = '&'.join(
            f'ping.{key}={value}'
            for key, value in sorted(ping_results.items()))

        tm = int(time.time())
        mqtt_string = (
            f'device_id={self._guid}&'
            f'{values}&'
            f'dbg_uptime={tm}&'
            f'dbg_version={__version__}').encode('ascii')

        await self._mqtt_publish(self._mqtt_topic, mqtt_string)

        log.info(f'Published: {values}')

    async def _mqtt_publish(self, topic, payload):
        if not self._mqttc:
            self._mqttc = MqttClient(self._mqtt_broker)
            await self._mqttc.connect()

        for i in (1, 2, 3):
            try:
                await self._mqttc.publish(topic, payload=payload)
            except MqttError:
                if i == 3:
                    raise
                try:
                    await self._mqttc.disconnect()
                except Exception:
                    log.exception('mqttc.disconnect()')
                await self._mqttc.connect()
            else:
                break


class Ping:
    @staticmethod
    def parse_ping_output(stdout):
        """
        ...
        1 packets transmitted, 1 received, 0% packet loss, time 0ms
        rtt min/avg/max/mdev = 8.699/8.699/8.699/0.000 ms
        """
        lines = stdout.strip().split('\n')
        assert len(lines) > 2, lines
        assert 'packets transmitted' in lines[-2], lines
        assert 'min/avg/max/mdev' in lines[-1], lines
        assert lines[-2].startswith(
            '1 packets transmitted, 1 received, 0% packet loss'), lines[-2]
        try:
            avg = lines[-1].split(' = ', 1)[1].split('/', 2)[1]
        except (IndexError, ValueError) as e:
            assert False, (e, lines[-1])
        return decimal.Decimal(avg)

    def __init__(self, name, ip):
        self.name = name
        if callable(ip):
            self.ip = ip()
        else:
            self.ip = ip
        self.ms = []

    def ping(self):
        try:
            out = subprocess.check_output(['ping', '-c1', self.ip])
        except subprocess.CalledProcessError:
            out = None
        else:
            out = self.parse_ping_output(out.decode('utf-8', 'replace'))
        self.ms.append(out)

    def as_result(self):
        not_nones = [value for value in self.ms if value is not None]
        if not_nones:
            avg = sum(not_nones) / len(not_nones)
            avg = avg.quantize(decimal.Decimal('.01'))
        else:
            avg = 0
        if len(not_nones) == len(self.ms):
            return f'{avg}'
        percentage = int(100 * (len(self.ms) - len(not_nones)) / len(self.ms))
        return f'{avg}/{percentage}'


def get_external_ip():
    return requests.get('http://ip4.osso.pub/').text.strip()


def get_gateway_ip():
    """
    1.2.3.4 via 192.168.1.1 dev wlp58s0 src 192.168.1.2 uid 1000
        cache
    """
    out = subprocess.check_output(['ip', 'route', 'get', '1.2.3.4'])
    out = out.decode('utf-8', 'replace')
    assert out.startswith('1.2.3.4 via '), out
    return out[12:].split(' ', 1)[0]


async def ping_all():
    HOSTS = {
        'dns.cfl': '1.1.1.1',
        'dns.ggl': '8.8.8.8',
        'ip.ext': (lambda: get_external_ip()),
        # "hop 1": $ ping -c1 -n -t 1 1.1.1.1
        # PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.
        # From 192.168.x.x icmp_seq=1 Time to live exceeded
        'gw.int': (lambda: get_gateway_ip()),
    }
    if 'PE32PING_GWEXT' in os.environ:
        # "hop 2": $ ping -c1 -n -t 2 1.1.1.1
        # PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.
        # From ext.ern.al.gw icmp_seq=1 Time to live exceeded
        HOSTS['gw.ext'] = os.environ['PE32PING_GWEXT']
    if 'PE32PING_HOST0' in os.environ:
        HOSTS['host.0'] = os.environ['PE32PING_HOST0']
    if 'PE32PING_HOST1' in os.environ:
        HOSTS['host.1'] = os.environ['PE32PING_HOST1']
    if 'PE32PING_HOST2' in os.environ:
        HOSTS['host.2'] = os.environ['PE32PING_HOST2']

    pings = 4
    res = dict((key, Ping(key, value)) for key, value in HOSTS.items())
    for try_ in range(1, pings + 1):
        for ping in res.values():
            ping.ping()  # XXX: not asyncio (yet)
        if try_ < pings:
            await asyncio.sleep(1)

    return dict((key, value.as_result()) for key, value in res.items())


async def main():
    publisher = Pe32PingPublisher()
    while True:
        ping_results = await ping_all()
        log.info(f'Ping results: {ping_results}')
        publisher.publish(ping_results)
        await asyncio.sleep(300)


if __name__ == '__main__':
    called_from_cli = (
        # Reading just JOURNAL_STREAM or INVOCATION_ID will not tell us
        # whether a user is looking at this, or whether output is passed to
        # systemd directly.
        any(os.isatty(i.fileno())
            for i in (sys.stdin, sys.stdout, sys.stderr)) or
        not os.environ.get('JOURNAL_STREAM'))
    sys.stdout.reconfigure(line_buffering=True)  # PYTHONUNBUFFERED, but better
    logging.basicConfig(
        level=(
            logging.DEBUG if os.environ.get('PE32ME162_DEBUG', '')
            else logging.INFO),
        format=(
            '%(asctime)s %(message)s' if called_from_cli
            else '%(message)s'),
        stream=sys.stdout,
        datefmt='%Y-%m-%d %H:%M:%S')

    print(f"pid {os.getpid()}: send SIGINT or SIGTERM to exit.")
    loop = asyncio.get_event_loop()
    main_coro = main()
    loop.run_until_complete(main_coro)
    loop.close()
    print('end of main')
