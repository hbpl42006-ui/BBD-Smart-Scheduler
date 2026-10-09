module.exports = {
  apps: [
    {
      name: "bbd-smart-scheduler-backend",
      cwd: "/var/www/bbd-smart-scheduler/backend",
      script: "/var/www/bbd-smart-scheduler/backend/venv/bin/gunicorn",
      args: "config.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 300 --access-logfile - --error-logfile -",
      interpreter: "none",
      exec_mode: "fork",
      instances: 1,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
      kill_timeout: 30000,
      env: {
        DJANGO_SETTINGS_MODULE: "config.settings",
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
