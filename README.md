# Meet Control

Painel local para enviar, remover e transcrever as gravações do MVP de Google Meet.

## Preparação (Linux com sessão gráfica e PulseAudio/PipeWire)

1. O robô `meet_bot.py` já acompanha o painel.
2. Instale as dependências do bot e o ffmpeg:

   ```bash
   pip install playwright faster-whisper
   playwright install chromium
   sudo apt install ffmpeg
   ```

3. Descubra a fonte de áudio de saída:

   ```bash
   pactl list short sources
   ```

4. Inicie o painel:

   ```bash
   python3 meet_control.py
   ```

Abra `http://127.0.0.1:8787`. O painel só aceita conexões locais.

## Uso

- Informe o link do Meet e confirme o consentimento; então use **Enviar robô**. A fonte `.monitor` do dispositivo de saída padrão é detectada automaticamente por PulseAudio/PipeWire.
- O painel não envia `--max-minutes`; ele solicita execução sem limite e define `MEET_MAX_MINUTES=0` para o MVP. Para isso ser efetivamente ilimitado, o `meet_bot.py` deve interpretar `0`/a variável como **sem limite**, em vez de aplicar um padrão de 60 minutos.
- Na primeira reunião, faça login manualmente na janela do Chromium. O perfil `perfil-meet/` persiste a sessão, sem senha no código.
- Em **Reuniões**, use **Remover robô** para encerrar o processo, sair da chamada, finalizar o áudio e iniciar a transcrição local automática.
- Configure “Pasta das gravações” com a pasta em que o seu `meet_bot.py` salva os arquivos. Após a reunião, clique em **Buscar arquivos**, selecione a gravação e use **Transcrever**. Os resultados `.txt`, `.srt` e `.json` ficam ao lado do áudio.

O painel passa `MEET_RECORDINGS_DIR` e `MEET_MAX_MINUTES=0` ao processo. O áudio `.monitor` é detectado automaticamente e `0` significa duração ilimitada.

Use somente em reuniões com aviso e autorização de todos os participantes.

## Railway

O projeto inclui `Dockerfile`, `start.sh` e `railway.toml`. O contêiner cria uma tela
virtual e uma saída PulseAudio virtual, permitindo que o Chromium reproduza o áudio
capturado pelo FFmpeg.

Configure no serviço:

- `PANEL_PASSWORD`: senha obrigatória recomendada para proteger o painel público.
- `MEET_BOT_NAME`: nome exibido pelo convidado no Meet.
- `WHISPER_MODEL`: modelo local, por padrão `small`.

Monte um volume persistente em `/app/storage`. Sem esse volume, perfil, gravações
e transcrições serão perdidos quando o serviço reiniciar. Nunca monte o volume em
`/app`, pois isso esconderia os arquivos do programa dentro do contêiner.

Em nuvem o robô entra como convidado. A reunião precisa permitir convidados e o
organizador pode precisar aprovar sua entrada. Um login Google manual não é viável
sem adicionar acesso remoto seguro à sessão gráfica.
