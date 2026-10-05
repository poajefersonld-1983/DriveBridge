"""Prepara o cliente OAuth próprio do DriveBridge antes de empacotar."""
import argparse
from drivebridge.core import bundled_oauth_client_file, read_oauth_client, write_json


def main():
    parser = argparse.ArgumentParser(description='Incluir o cliente OAuth do DriveBridge na distribuição.')
    parser.add_argument('json', help='JSON do cliente OAuth Google do tipo Aplicativo para computador')
    args = parser.parse_args()
    write_json(bundled_oauth_client_file(), read_oauth_client(args.json))
    print('Cliente OAuth configurado. O login por e-mail já pode abrir a autorização no navegador.')


if __name__ == '__main__':
    main()
