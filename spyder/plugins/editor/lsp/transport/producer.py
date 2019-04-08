# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# Copyright (c) 2009- Spyder Project Contributors
#
# Distributed under the terms of the MIT License
# (see spyder/__init__.py for details)
# -----------------------------------------------------------------------------


"""
Spyder MS Language Server Protocol v3.0 transport proxy implementation.

This module handles incoming requests from the actual Spyder LSP client ZMQ
queue, encapsulates them into valid JSONRPC messages and sends them to an
LSP server via TCP.
"""

# Standard library imports
import json
import logging
import os
import socket
import subprocess
import sys
import time

# Third party imports
import zmq

# Local imports
from spyder.plugins.editor.lsp.transport.consumer import IncomingMessageThread
from spyder.py3compat import getcwd


TIMEOUT = 5000
PID = os.getpid()


logger = logging.getLogger(__name__)


class LanguageServerClient:
    """Implementation of a v3.0 compilant language server client."""
    CONTENT_LENGTH = 'Content-Length: {0}\r\n\r\n'
    MAX_TIMEOUT_TIME = 20000

    def __init__(self, host='127.0.0.1', port=2087, workspace=getcwd(),
                 use_external_server=False, zmq_in_port=7000,
                 zmq_out_port=7001, server='pyls', server_args=['--tcp']):
        self.req_status = {}
        self.host = host
        self.port = port
        self.workspace = workspace
        # self.request_seq = 1

        self.server = None
        self.is_local_server_running = not use_external_server
        if not use_external_server:
            logger.info('Starting server: {0} {1} on {2}:{3}'.format(
                server, ' '.join(server_args), self.host, self.port))
            exec_line = [sys.executable, '-m', server] + server_args
            logger.info(' '.join(exec_line))

            self.server = subprocess.Popen(
                exec_line,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE)

        logger.info('Connecting to language server at {0}:{1}'.format(
            self.host, self.port))

        connected = False
        initial_time = time.time()
        connection_error = None
        while not connected:
            try:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.connect((self.host, int(self.port)))
                connected = True
            except Exception as e:
                connection_error = e

            if time.time() - initial_time > self.MAX_TIMEOUT_TIME:
                break

        if not connected:
            logger.error("The client was unable to establish a connection "
                         "with the Language Server. The error was: "
                         "{}".format(connection_error))
            raise Exception("An error occurred while trying to create a "
                            "client to connect to the Language Server! The "
                            "error was\n\n{}".format(connection_error))

        self.socket.setblocking(True)

        logger.info('Starting ZMQ connection...')
        self.context = zmq.Context()
        self.zmq_in_socket = self.context.socket(zmq.PAIR)
        self.zmq_in_socket.connect("tcp://localhost:{0}".format(zmq_in_port))
        self.zmq_out_socket = self.context.socket(zmq.PAIR)
        self.zmq_out_socket.connect("tcp://localhost:{0}".format(zmq_out_port))
        logger.info('Sending server_ready...')
        self.zmq_out_socket.send_pyobj({'id': -1, 'method': 'server_ready',
                                        'params': {}})

        logger.info('Creating consumer Thread...')
        self.reading_thread = IncomingMessageThread()
        self.reading_thread.initialize(self.socket, self.zmq_out_socket,
                                       self.req_status)

    def start(self):
        self.reading_thread.start()
        logger.info('Ready to receive/attend requests and responses!')

    def stop(self):
        logger.info('Closing TCP socket...')
        self.socket.close()
        if self.is_local_server_running:
            logger.info('Closing language server process...')
            self.server.terminate()
        logger.info('Closing consumer thread...')
        self.reading_thread.stop()
        logger.debug('Joining thread...')
        self.reading_thread.join()
        logger.debug('Exit routine should be complete')

    def listen(self):
        events = self.zmq_in_socket.poll(TIMEOUT)
        # requests = []
        while events > 0:
            client_request = self.zmq_in_socket.recv_pyobj()
            logger.debug("Client Event: {0}".format(client_request))
            server_request = self.__compose_request(client_request['id'],
                                                    client_request['method'],
                                                    client_request['params'])
            self.__send_request(server_request)
            # self.zmq_socket.send_pyobj({'a': 'b'})
            events -= 1

    def __compose_request(self, id, method, params):
        request = {
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params
        }
        return request

    def __send_request(self, request):
        json_req = json.dumps(request)
        content = bytes(json_req.encode('utf-8'))
        content_length = len(content)

        logger.debug('Sending request of type: {0}'.format(request['method']))
        logger.debug(json_req)

        content_length = self.CONTENT_LENGTH.format(
            content_length).encode('utf-8')
        self.socket.send(bytes(content_length))
        self.socket.send(content)
        # self.request_seq += 1