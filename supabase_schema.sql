-- Сначала удаляем всё старое
DROP TABLE IF EXISTS num_registry CASCADE;
DROP TABLE IF EXISTS gift_transfers CASCADE;
DROP TABLE IF EXISTS star_transactions CASCADE;
DROP TABLE IF EXISTS inventory CASCADE;
DROP TABLE IF EXISTS ratings CASCADE;
DROP TABLE IF EXISTS gifts CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP FUNCTION IF EXISTS generate_user_num CASCADE;
DROP FUNCTION IF EXISTS update_rating_after_gift CASCADE;

-- ==========================================
-- OWPENGRAM BACKEND — Supabase Schema
-- ==========================================

-- Пользователи
CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    num         TEXT UNIQUE NOT NULL DEFAULT '',
    username    TEXT UNIQUE,
    display_name TEXT,
    avatar_url  TEXT,
    password_hash TEXT NOT NULL,
    star_balance BIGINT DEFAULT 0,
    rating      BIGINT DEFAULT 0,
    is_admin    BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Гифты (библиотека)
CREATE TABLE gifts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    description TEXT,
    lottie_url  TEXT NOT NULL,
    preview_url TEXT,
    price_stars BIGINT NOT NULL DEFAULT 1,
    rarity      TEXT DEFAULT 'common',
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Инвентарь пользователя
CREATE TABLE inventory (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id    UUID REFERENCES users(id) ON DELETE CASCADE,
    gift_id     UUID REFERENCES gifts(id),
    received_from UUID REFERENCES users(id),
    received_at TIMESTAMPTZ DEFAULT NOW(),
    is_displayed BOOLEAN DEFAULT TRUE
);

-- История транзакций звёзд
CREATE TABLE star_transactions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES users(id) ON DELETE CASCADE,
    amount      BIGINT NOT NULL,
    reason      TEXT,
    ref_id      UUID,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Отправленные гифты
CREATE TABLE gift_transfers (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_user   UUID REFERENCES users(id),
    to_user     UUID REFERENCES users(id),
    gift_id     UUID REFERENCES gifts(id),
    stars_spent BIGINT NOT NULL,
    message     TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Рейтинг
CREATE TABLE ratings (
    user_id     UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    total_stars_sent    BIGINT DEFAULT 0,
    total_gifts_sent    BIGINT DEFAULT 0,
    total_gifts_received BIGINT DEFAULT 0,
    score       BIGINT DEFAULT 0,
    rank        INTEGER,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Реестр кастомных номеров
CREATE TABLE num_registry (
    num         TEXT PRIMARY KEY,
    issued_to   UUID REFERENCES users(id),
    issued_by   UUID REFERENCES users(id),
    issued_at   TIMESTAMPTZ DEFAULT NOW(),
    note        TEXT
);

-- ==========================================
-- Функции и триггеры
-- ==========================================

-- Авто-генерация номера #XXXXXXXX
CREATE OR REPLACE FUNCTION generate_user_num()
RETURNS TRIGGER AS $$
DECLARE
    new_num TEXT;
BEGIN
    LOOP
        new_num := '#' || LPAD(FLOOR(RANDOM() * 99999999)::TEXT, 8, '0');
        EXIT WHEN NOT EXISTS (SELECT 1 FROM users WHERE num = new_num);
    END LOOP;
    NEW.num := new_num;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_user_num
BEFORE INSERT ON users
FOR EACH ROW
WHEN (NEW.num IS NULL OR NEW.num = '')
EXECUTE FUNCTION generate_user_num();

-- Авто-обновление рейтинга после отправки гифта
CREATE OR REPLACE FUNCTION update_rating_after_gift()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO ratings (user_id, total_stars_sent, total_gifts_sent, score)
    VALUES (NEW.from_user, NEW.stars_spent, 1, NEW.stars_spent)
    ON CONFLICT (user_id) DO UPDATE SET
        total_stars_sent = ratings.total_stars_sent + NEW.stars_spent,
        total_gifts_sent = ratings.total_gifts_sent + 1,
        score = ratings.score + NEW.stars_spent,
        updated_at = NOW();

    INSERT INTO ratings (user_id, total_gifts_received, score)
    VALUES (NEW.to_user, 1, 10)
    ON CONFLICT (user_id) DO UPDATE SET
        total_gifts_received = ratings.total_gifts_received + 1,
        score = ratings.score + 10,
        updated_at = NOW();

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER on_gift_transfer
AFTER INSERT ON gift_transfers
FOR EACH ROW EXECUTE FUNCTION update_rating_after_gift();
