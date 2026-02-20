<?php
declare(strict_types=1);

function json_out(int $status, array $data): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store, no-cache, must-revalidate');
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    json_out(405, ['ok' => false, 'error' => 'method_not_allowed']);
}

$config_path = __DIR__ . '/config.php';
if (!is_file($config_path)) {
    json_out(500, ['ok' => false, 'error' => 'missing_config']);
}

$cfg = require $config_path;
if (!is_array($cfg)) {
    json_out(500, ['ok' => false, 'error' => 'invalid_config']);
}

$openai_key = trim((string)($cfg['openai_api_key'] ?? ''));
$relay_token = trim((string)($cfg['relay_token'] ?? ''));

if ($openai_key === '') {
    json_out(500, ['ok' => false, 'error' => 'missing_openai_key']);
}

if ($relay_token !== '') {
    $xrelay = (string)($_SERVER['HTTP_X_RELAY_TOKEN'] ?? '');
    if ($xrelay !== '' && hash_equals($relay_token, trim($xrelay))) {
        $auth = 'ok';
    } else {
        $auth = '';
    }
    if ($auth === '') {
        $auth = (string)($_SERVER['HTTP_AUTHORIZATION'] ?? ($_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? ''));
    }
    if ($auth === '' && function_exists('getallheaders')) {
        $headers = getallheaders();
        if (is_array($headers)) {
            foreach ($headers as $k => $v) {
                if (strtolower((string)$k) === 'authorization') {
                    $auth = (string)$v;
                    break;
                }
                if (strtolower((string)$k) === 'x-relay-token' && hash_equals($relay_token, trim((string)$v))) {
                    $auth = 'ok';
                    break;
                }
            }
        }
    }
    if ($auth !== 'ok') {
        if (!preg_match('/^Bearer\\s+(.+)$/i', $auth, $m)) {
            json_out(401, ['ok' => false, 'error' => 'unauthorized']);
        }
        $provided = trim((string)$m[1]);
        if ($provided === '' || !hash_equals($relay_token, $provided)) {
            json_out(401, ['ok' => false, 'error' => 'unauthorized']);
        }
    }
}

$raw = file_get_contents('php://input');
if (!is_string($raw) || $raw === '') {
    json_out(400, ['ok' => false, 'error' => 'empty_body']);
}

$in = json_decode($raw, true);
if (!is_array($in)) {
    json_out(400, ['ok' => false, 'error' => 'invalid_json']);
}

$mode = (string)($in['mode'] ?? 'chat');
$model = trim((string)($in['model'] ?? 'gpt-4o-mini'));
$temperature = isset($in['temperature']) ? (float)$in['temperature'] : 0.8;
$max_tokens = isset($in['max_tokens']) ? (int)$in['max_tokens'] : 1200;
$system_prompt = (string)($in['system_prompt'] ?? '');
$user_prompt = (string)($in['user_prompt'] ?? '');

if ($model === '') {
    $model = 'gpt-4o-mini';
}
if ($max_tokens < 1) {
    $max_tokens = 600;
}
if ($max_tokens > 4096) {
    $max_tokens = 4096;
}

$messages = [];
if ($mode === 'vision') {
    $image_mime = trim((string)($in['image_mime'] ?? 'image/jpeg'));
    $image_b64 = trim((string)($in['image_base64'] ?? ''));
    if ($image_b64 === '') {
        json_out(400, ['ok' => false, 'error' => 'missing_image_base64']);
    }
    $messages = [
        ['role' => 'system', 'content' => $system_prompt],
        [
            'role' => 'user',
            'content' => [
                ['type' => 'text', 'text' => $user_prompt],
                ['type' => 'image_url', 'image_url' => ['url' => "data:{$image_mime};base64,{$image_b64}"]],
            ],
        ],
    ];
} else {
    $messages = [
        ['role' => 'system', 'content' => $system_prompt],
        ['role' => 'user', 'content' => $user_prompt],
    ];
}

$payload = [
    'model' => $model,
    'messages' => $messages,
    'temperature' => $temperature,
    'max_tokens' => $max_tokens,
];

$ch = curl_init('https://api.openai.com/v1/chat/completions');
curl_setopt($ch, CURLOPT_POST, true);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 20);
curl_setopt($ch, CURLOPT_TIMEOUT, 180);
curl_setopt($ch, CURLOPT_HTTPHEADER, [
    'Content-Type: application/json',
    'Authorization: Bearer ' . $openai_key,
]);
curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));

$response = curl_exec($ch);
if ($response === false) {
    $err = curl_error($ch);
    curl_close($ch);
    json_out(502, ['ok' => false, 'error' => 'curl_error', 'detail' => $err]);
}

$status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);

$decoded = json_decode((string)$response, true);
if (!is_array($decoded)) {
    $decoded = [];
}

if ($status < 200 || $status >= 300) {
    $detail = '';
    if (isset($decoded['error']['message']) && is_string($decoded['error']['message'])) {
        $detail = $decoded['error']['message'];
    }
    json_out(502, ['ok' => false, 'error' => 'openai_error', 'status' => $status, 'detail' => $detail]);
}

$text = trim((string)($decoded['choices'][0]['message']['content'] ?? ''));
if ($text === '') {
    json_out(502, ['ok' => false, 'error' => 'empty_openai_text']);
}

json_out(200, ['ok' => true, 'text' => $text]);
