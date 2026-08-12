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
