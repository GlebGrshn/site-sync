<?php
/**
 * Приём заявок «Узнать цену» со страницы catalog.html.
 *
 * Заявка всегда сохраняется в файл — даже если почта и Telegram не настроены,
 * ни одна заявка не теряется. Настройки уведомлений: lead.config.php
 * (создаётся копированием lead.config.example.php).
 */

header('Content-Type: application/json; charset=utf-8');
header('X-Content-Type-Options: nosniff');

const MAX_NAME = 60;
const MAX_PHONE = 30;
const MIN_DIGITS = 10;
const RATE_LIMIT_SECONDS = 20;

/** Настройки уведомлений. Файла может не быть — это нормально. */
function loadConfig()
{
    $defaults = [
        'email_to' => '',
        'email_from' => '',
        'telegram_token' => '',
        'telegram_chat_id' => '',
    ];
    $file = __DIR__ . '/lead.config.php';
    if (is_file($file)) {
        $loaded = include $file;
        if (is_array($loaded)) {
            return array_merge($defaults, $loaded);
        }
    }
    return $defaults;
}

/**
 * Каталог для заявок. Сначала пробуем положить его ВЫШЕ корня сайта —
 * тогда файл с телефонами физически недоступен из браузера.
 */
function storageDir()
{
    $outside = dirname(__DIR__) . '/pomni-m-leads';
    if (is_dir($outside) || @mkdir($outside, 0700, true)) {
        if (is_writable($outside)) {
            return $outside;
        }
    }

    // запасной вариант — внутри сайта, но закрытый от скачивания
    $inside = __DIR__ . '/leads';
    if (!is_dir($inside)) {
        @mkdir($inside, 0700, true);
    }
    $htaccess = $inside . '/.htaccess';
    if (is_dir($inside) && !is_file($htaccess)) {
        @file_put_contents($htaccess, implode("\n", [
            '<IfModule mod_authz_core.c>',
            'Require all denied',
            '</IfModule>',
            '<IfModule !mod_authz_core.c>',
            'Order allow,deny',
            'Deny from all',
            '</IfModule>',
            '',
        ]));
    }
    return $inside;
}

function fail($message, $code = 400)
{
    http_response_code($code);
    echo json_encode(['ok' => false, 'error' => $message], JSON_UNESCAPED_UNICODE);
    exit;
}

function clientIp()
{
    return isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : '';
}

/** Поле формы. Форма уходит как multipart/form-data, поэтому берём из $_POST. */
function field($name)
{
    return isset($_POST[$name]) && is_string($_POST[$name]) ? trim($_POST[$name]) : '';
}

/* mbstring есть почти везде, но если вдруг нет — не падаем с фатальной ошибкой */
if (!function_exists('mb_strlen')) {
    function mb_strlen($value, $encoding = null)
    {
        return strlen($value);
    }
}
if (!function_exists('mb_substr')) {
    function mb_substr($value, $start, $length = null, $encoding = null)
    {
        return $length === null ? substr($value, $start) : substr($value, $start, $length);
    }
}

/** Грубая защита от повторных отправок с одного адреса. */
function rateLimited($dir)
{
    $ip = clientIp();
    if ($ip === '') {
        return false;
    }
    $marker = $dir . '/.rate-' . md5($ip);
    if (is_file($marker) && (time() - filemtime($marker)) < RATE_LIMIT_SECONDS) {
        return true;
    }
    @file_put_contents($marker, '1');
    return false;
}

$config = loadConfig();
$dir = storageDir();

/* ---------- проверка работоспособности ---------- */

if ($_SERVER['REQUEST_METHOD'] === 'GET') {
    echo json_encode([
        'ok' => true,
        'php' => PHP_MAJOR_VERSION . '.' . PHP_MINOR_VERSION,
        'storage_writable' => is_dir($dir) && is_writable($dir),
        'storage_outside_webroot' => strpos($dir, __DIR__) !== 0,
        'email_configured' => $config['email_to'] !== '',
        'telegram_configured' => $config['telegram_token'] !== '' && $config['telegram_chat_id'] !== '',
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    fail('Метод не поддерживается', 405);
}

/* ---------- разбор и проверка ---------- */

// поле-ловушка: его заполняют только боты. Отвечаем «успех», чтобы бот
// не начал подбирать другую форму отправки.
if (field('website') !== '') {
    echo json_encode(['ok' => true], JSON_UNESCAPED_UNICODE);
    exit;
}

$name = field('name');
$phone = field('phone');
$item = field('item');
$options = field('options');
$consent = field('consent');

$name = preg_replace('/[\x00-\x1F\x7F]/u', '', $name);
$phone = preg_replace('/[^\d\+\-\(\)\s]/u', '', $phone);
$item = preg_replace('/[\x00-\x1F\x7F]/u', '', $item);
$options = preg_replace('/[\x00-\x1F\x7F]/u', '', $options);

if (mb_strlen($name) < 2 || mb_strlen($name) > MAX_NAME) {
    fail('Проверьте имя');
}
if (mb_strlen($phone) > MAX_PHONE || preg_match_all('/\d/', $phone) < MIN_DIGITS) {
    fail('Проверьте номер телефона');
}
if (!$consent) {
    fail('Нужно согласие на обработку персональных данных');
}
if (!is_dir($dir) || !is_writable($dir)) {
    fail('Хранилище заявок недоступно', 500);
}
if (rateLimited($dir)) {
    fail('Заявка уже отправлена, подождите немного', 429);
}

$item = $item !== '' ? mb_substr($item, 0, 120) : 'не указана';
$options = mb_substr($options, 0, 200);

/* ---------- сохранение ---------- */

$row = [
    date('Y-m-d H:i:s'),
    $name,
    $phone,
    $item,
    $options,
    clientIp(),
    mb_substr((string)(isset($_SERVER['HTTP_USER_AGENT']) ? $_SERVER['HTTP_USER_AGENT'] : ''), 0, 200),
];

$file = $dir . '/leads.csv';
$isNew = !is_file($file);
$handle = @fopen($file, 'a');
if (!$handle) {
    fail('Не удалось сохранить заявку', 500);
}
if (flock($handle, LOCK_EX)) {
    if ($isNew) {
        fwrite($handle, "\xEF\xBB\xBF"); // BOM, чтобы Excel открыл кириллицу
        fputcsv($handle, ['Дата', 'Имя', 'Телефон', 'Позиция', 'Модификаторы', 'IP', 'Браузер'], ';');
    }
    fputcsv($handle, $row, ';');
    flock($handle, LOCK_UN);
}
fclose($handle);
@chmod($file, 0600);

/* ---------- уведомления ---------- */

$delivered = [];

if ($config['email_to'] !== '') {
    $subject = '=?UTF-8?B?' . base64_encode('Заявка с сайта: ' . $item) . '?=';
    $body = "Новая заявка с сайта pomni-m.ru\n\n"
        . "Позиция: {$item}\n"
        . ($options !== '' ? "Выбрано: {$options}\n" : '')
        . "Имя: {$name}\n"
        . "Телефон: {$phone}\n"
        . "Дата: {$row[0]}\n";
    $from = $config['email_from'] !== '' ? $config['email_from'] : 'noreply@pomni-m.ru';
    $headers = "From: Сайт Помним <{$from}>\r\n"
        . "Content-Type: text/plain; charset=UTF-8\r\n";
    if (@mail($config['email_to'], $subject, $body, $headers)) {
        $delivered[] = 'email';
    }
}

if ($config['telegram_token'] !== '' && $config['telegram_chat_id'] !== '') {
    $text = "📩 Заявка с сайта pomni-m.ru\n\n"
        . "Позиция: {$item}\n"
        . ($options !== '' ? "Выбрано: {$options}\n" : '')
        . "Имя: {$name}\n"
        . "Телефон: {$phone}";
    $url = 'https://api.telegram.org/bot' . $config['telegram_token'] . '/sendMessage';
    $payload = http_build_query([
        'chat_id' => $config['telegram_chat_id'],
        'text' => $text,
    ]);
    if (function_exists('curl_init')) {
        $curl = curl_init($url);
        curl_setopt_array($curl, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $payload,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => 6,
        ]);
        $response = curl_exec($curl);
        if ($response !== false && curl_getinfo($curl, CURLINFO_HTTP_CODE) === 200) {
            $delivered[] = 'telegram';
        }
        curl_close($curl);
    }
}

echo json_encode(['ok' => true, 'delivered' => $delivered], JSON_UNESCAPED_UNICODE);
