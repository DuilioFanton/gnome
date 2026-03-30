# Catalina Dynamic Wallpaper (GNOME)

Truly dynamic sunrise/sunset wallpaper for GNOME.

Wallpaper dinâmico para GNOME que recalcula os horários do slideshow com base em nascer/pôr do sol da sua localização atual.

## Como funciona

- Detecta localização automaticamente por IP via `https://ipapi.co/json/`.
- Consulta eventos solares via `https://api.sunrise-sunset.org/json`.
- Regera o arquivo `Catalina-timed.xml` diariamente (e no boot) com durações variáveis.
- Reaplica o wallpaper via `gsettings` (`picture-uri` e `picture-uri-dark`).
- Em caso de falha da API, mantém o XML anterior (não quebra o wallpaper).

## Requisitos

- Linux com GNOME
- `python3` (3.10+ recomendado)
- `systemd --user`
- Internet para consultar as APIs

## Estrutura esperada

Este projeto foi feito para ficar em:

`~/.local/share/backgrounds/gnome`

Arquivos principais:

- `update_catalina_wallpaper.py`
- `Catalina-timed.xml`
- `Catalina-timed/` com 9 imagens (`Catalina-1.jpg` ... `Catalina-9.jpg`)
- `catalina-dynamic-wallpaper.service`
- `catalina-dynamic-wallpaper.timer`

## Instalação

No diretório do projeto (`~/.local/share/backgrounds/gnome`):

```bash
chmod +x update_catalina_wallpaper.py
mkdir -p ~/.config/systemd/user
install -m 0644 catalina-dynamic-wallpaper.service ~/.config/systemd/user/catalina-dynamic-wallpaper.service
install -m 0644 catalina-dynamic-wallpaper.timer ~/.config/systemd/user/catalina-dynamic-wallpaper.timer
systemctl --user daemon-reload
systemctl --user enable --now catalina-dynamic-wallpaper.timer
systemctl --user start catalina-dynamic-wallpaper.service
```

## Verificação

```bash
systemctl --user status catalina-dynamic-wallpaper.timer --no-pager
systemctl --user status catalina-dynamic-wallpaper.service --no-pager
systemctl --user list-timers catalina-dynamic-wallpaper.timer --no-pager
python3 update_catalina_wallpaper.py --verbose
```

## Uso manual

- Atualizar agora com localização por IP:

```bash
python3 update_catalina_wallpaper.py --verbose
```

- Usar coordenadas fixas (sem IP):

```bash
python3 update_catalina_wallpaper.py --lat -23.5505 --lon -46.6333 --tz America/Sao_Paulo --verbose
```

## Agendamento

O timer atual está configurado para:

- `OnBootSec=2min` (roda 2 minutos após login/boot)
- `OnCalendar=*-*-* 00/6:05:00` (a cada 6 horas)
- `RandomizedDelaySec=5min` (evita pico exato)

Para mudar frequência, edite `catalina-dynamic-wallpaper.timer`, depois rode:

```bash
systemctl --user daemon-reload
systemctl --user restart catalina-dynamic-wallpaper.timer
```

## Desinstalação

```bash
systemctl --user disable --now catalina-dynamic-wallpaper.timer
rm -f ~/.config/systemd/user/catalina-dynamic-wallpaper.timer
rm -f ~/.config/systemd/user/catalina-dynamic-wallpaper.service
systemctl --user daemon-reload
```

## Privacidade e observações

- O modo automático usa seu IP público para inferir cidade/latitude/longitude.
- O XML precisa de caminhos absolutos para as imagens no GNOME (usar `~` dentro do XML pode falhar).
- A saída em `--verbose` mostra o caminho do XML com `~` para não expor o usuário no log.
