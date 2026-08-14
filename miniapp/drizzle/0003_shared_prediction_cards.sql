-- Tarjetas pre-match compartibles por link (imagen con marca de agua).
--
-- `payload` guarda la tarjeta ya resuelta, no el fixture: el link comparte lo
-- que el modelo dijo antes del kickoff, asi que reabrirlo no puede devolver
-- numeros distintos. Tambien mantiene la imagen fuera del camino critico del
-- backend -servirla no vuelve a llamar a `/v1/predict/upcoming`-, que importa
-- cuando el link circula y cada vista previa dispara una peticion.
--
-- Una tarjeta por partido (`fixture_key` es la clave): la primera vez que
-- alguien comparte un partido se congela, y quien lo comparta despues difunde
-- exactamente la misma imagen. Dos tarjetas del mismo partido con cifras
-- distintas -congeladas con horas de diferencia- circulando a la vez seria
-- indefendible para un producto cuya premisa es la prediccion sellada.
CREATE TABLE IF NOT EXISTS "shared_prediction_cards" (
  "fixture_key" text PRIMARY KEY,
  "token" text NOT NULL,
  "league_slug" text NOT NULL,
  "match_id" bigint NOT NULL,
  "home_team_name" text NOT NULL,
  "away_team_name" text NOT NULL,
  "kickoff_ts" timestamptz NOT NULL,
  "payload" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "created_by" bigint,
  "created_at" timestamptz NOT NULL DEFAULT now()
);

-- La lectura publica entra siempre por el token, nunca por el fixture.
CREATE UNIQUE INDEX IF NOT EXISTS "shared_prediction_cards_token_uidx"
  ON "shared_prediction_cards" ("token");

-- `created_by` no tiene FK a `miniapp_users` a proposito: es trazabilidad de
-- quien congelo la tarjeta, y borrar una cuenta no debe romper links que ya
-- circulan por fuera de la aplicacion.
