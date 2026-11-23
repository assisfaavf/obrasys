# Obrasys no Windows

Abra o atalho Obrasys na Area de Trabalho. O iniciador sobe os servicos do
Docker Compose, aguarda o Django e abre o Edge em modo aplicativo. Fechar a
janela mantem os servicos locais rodando.

O iniciador usa `.env` e, quando presente, `.env.desktop`. Configure
`OBRASYS_LOCAL_DB` em `.env.desktop` para selecionar um banco no PostgreSQL
local. O Compose do desktop publica a interface em `127.0.0.1` e mantem
o PostgreSQL na rede interna dos containers.

Para validar a inicializacao sem abrir a janela, rode na raiz do projeto:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File desktop\Start-Obrasys.ps1 -CheckOnly
```

O log fica em `%LOCALAPPDATA%\Obrasys\inicio.log`. Para parar os servicos
locais mantendo os dados, rode:

```powershell
docker compose -f docker-compose.yml -f docker-compose.desktop.yml stop
```

Para recompilar o iniciador, rode na raiz do projeto:

```powershell
& "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /out:desktop\Obrasys.exe /win32icon:app\static\img\obrasys-logo-capacete-branco.ico /reference:System.Windows.Forms.dll desktop\Obrasys.cs
```
