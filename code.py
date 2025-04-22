import os
import sys
import datetime
import subprocess
import shutil
import re
import glob

# Импорт библиотек с проверкой
try:
    from faster_whisper import WhisperModel
    from moviepy.video.io.VideoFileClip import VideoFileClip
    import srt
except ImportError as e:
    print(f"Ошибка: {e}")
    print("Убедитесь, что установлены необходимые библиотеки:")
    print("pip install faster-whisper moviepy srt")
    sys.exit(1)

# === Настройки ===
VIDEO_PATH = "Algebra.mp4"
CLIP_COUNT = 3
MODEL_SIZE = "base"
MIN_CLIP_DURATION = 60  # Минимальная продолжительность клипа в секундах
MAX_CLIP_DURATION = 300  # Максимальная продолжительность клипа в секундах
MIN_PAUSE_DURATION = 1.0  # Минимальная продолжительность паузы между предложениями

# Проверка необходимых условий
if not os.path.exists(VIDEO_PATH):
    print(f"Ошибка: Файл {VIDEO_PATH} не найден.")
    sys.exit(1)

if not shutil.which("ffmpeg"):
    print("Ошибка: ffmpeg не установлен в системе.")
    sys.exit(1)

def extract_segments(video_path):
    """Извлекает сегменты речи из видео"""
    print("[1/5] Загружаем модель...")
    model = WhisperModel(MODEL_SIZE)
    
    print("[2/5] Распознаём речь...")
    segments, _ = model.transcribe(video_path)
    
    print("[3/5] Генерируем субтитры...")
    srt_segments = []
    for i, segment in enumerate(segments):
        start = datetime.timedelta(seconds=segment.start)
        end = datetime.timedelta(seconds=segment.end)
        content = segment.text.strip()
        srt_segments.append(srt.Subtitle(index=i + 1, start=start, end=end, content=content))
    
    # Сохраняем все субтитры
    full_srt = srt.compose(srt_segments)
    with open("full_subs.srt", "w", encoding="utf-8") as f:
        f.write(full_srt)
    
    return srt_segments

def find_split_points(srt_segments, duration, clip_count):
    """Находит оптимальные точки для разбивки видео"""
    print("[4/5] Анализируем точки разбиения по смыслу...")
    
    # Конвертируем сегменты субтитров в более удобный формат
    segments_data = []
    for seg in srt_segments:
        segments_data.append({
            'start': seg.start.total_seconds(),
            'end': seg.end.total_seconds(),
            'text': seg.content,
            'pause_after': 0
        })
    
    # Вычисляем паузы между сегментами
    for i in range(len(segments_data) - 1):
        segments_data[i]['pause_after'] = segments_data[i+1]['start'] - segments_data[i]['end']
    
    # Оцениваем каждый сегмент как потенциальную точку разделения
    split_scores = []
    for i, seg in enumerate(segments_data):
        # Пропускаем слишком ранние сегменты
        if seg['end'] < MIN_CLIP_DURATION:
            continue
            
        score = 0
        
        # Бонус за паузу после сегмента
        if seg['pause_after'] > MIN_PAUSE_DURATION:
            score += seg['pause_after'] * 10
        
        # Бонус за завершение предложения
        if i > 0 and re.search(r'[.!?]$', seg['text']):
            score += 50
        
        # Бонус за оптимальное положение (равномерное распределение)
        relative_position = seg['end'] / duration
        ideal_positions = [(i+1)/clip_count for i in range(clip_count-1)]
        position_score = max([100 - abs(relative_position - pos) * 300 for pos in ideal_positions])
        score += position_score
        
        split_scores.append((i, score, seg['end']))
    
    # Сортируем по оценке и выбираем лучшие точки
    split_scores.sort(key=lambda x: x[1], reverse=True)
    
    best_splits = []
    for i in range(clip_count - 1):
        if i < len(split_scores):
            best_splits.append(split_scores[i][2])
    
    # Сортируем точки по времени
    best_splits.sort()
    
    # Корректируем точки разделения
    corrected_splits = []
    last_time = 0
    
    for split_time in best_splits:
        # Корректировка по минимальной длительности
        if split_time - last_time < MIN_CLIP_DURATION:
            split_time = last_time + MIN_CLIP_DURATION
        # Корректировка по максимальной длительности
        if split_time - last_time > MAX_CLIP_DURATION:
            split_time = last_time + MAX_CLIP_DURATION
        
        corrected_splits.append(split_time)
        last_time = split_time
    
    # Добавляем конец видео
    corrected_splits.append(duration)
    
    # Генерируем диапазоны для клипов
    clip_ranges = []
    start_time = 0
    for end_time in corrected_splits:
        clip_ranges.append((start_time, end_time))
        start_time = end_time
    
    print(f"Точки разделения (в секундах): {[end for _, end in clip_ranges]}")
    
    return clip_ranges

def create_clip(video, clip_start, clip_end, clip_path):
    """Создает клип с указанными параметрами"""
    try:
        # Пробуем через MoviePy
        clip = video.subclipped(clip_start, clip_end)
        clip.write_videofile(clip_path, codec="libx264", audio_codec="aac")
        clip.close()
        return True
    except Exception as e:
        print(f"Ошибка MoviePy: {e}")
        print("Используем ffmpeg напрямую...")
        
        try:
            # Преобразуем секунды в формат HH:MM:SS.ms
            duration = clip_end - clip_start
            duration_str = str(datetime.timedelta(seconds=duration))
            start_str = str(datetime.timedelta(seconds=clip_start))
            
            # Запускаем ffmpeg
            subprocess.run([
                "ffmpeg", "-y",
                "-ss", start_str,
                "-i", VIDEO_PATH,
                "-t", duration_str,
                "-c:v", "libx264", "-c:a", "aac",
                "-avoid_negative_ts", "1",
                clip_path
            ], check=True, stderr=subprocess.PIPE)
            
            print(f"Клип создан через ffmpeg: {clip_path}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"Ошибка ffmpeg: {e}")
            return False

def create_subtitles(srt_segments, clip_start, clip_end, clip_index):
    """Создает файл субтитров для клипа"""
    # Готовим временные метки
    start_td = datetime.timedelta(seconds=clip_start)
    end_td = datetime.timedelta(seconds=clip_end)
    
    # Отбираем субтитры для данного временного отрезка
    clip_srt_segments = [s for s in srt_segments if start_td <= s.start < end_td]
    
    # Если субтитры не найдены, создаем пустой файл
    if not clip_srt_segments:
        srt_path = f"output/clip_{clip_index}.srt"
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("")
        print(f"Предупреждение: Для clip_{clip_index} не найдено субтитров.")
        return srt_path
    
    # Перенумеруем субтитры
    for idx, s in enumerate(clip_srt_segments):
        s.index = idx + 1
    
    # Сдвигаем субтитры на 0
    for s in clip_srt_segments:
        s.start -= start_td
        s.end -= start_td
    
    # Сохраняем субтитры
    srt_text = srt.compose(clip_srt_segments)
    srt_path = f"output/clip_{clip_index}.srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_text)
    
    return srt_path

def embed_subtitles(clip_path, srt_path, output_path):
    """Встраивает субтитры в видео"""
    try:
        abs_srt_path = os.path.abspath(srt_path).replace("'", "\\'").replace(" ", "\\ ")
        abs_clip_path = os.path.abspath(clip_path)
        abs_output_path = os.path.abspath(output_path)
        
        subprocess.run([
            "ffmpeg", "-y", "-i", abs_clip_path, 
            "-vf", f"subtitles={abs_srt_path}", 
            "-c:v", "libx264", "-c:a", "copy", 
            abs_output_path
        ], check=True, stderr=subprocess.PIPE)
        
        print(f"Создан файл с субтитрами: {output_path}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Ошибка при добавлении субтитров: {e}")
        return False

def cleanup_temp_files():
    """Удаляет временные файлы, созданные MoviePy"""
    print("Удаление временных файлов...")
    temp_patterns = [
        "output/*TEMP*", 
        "*TEMP*", 
        "output/*.melt",
        "*.melt"
    ]
    
    count = 0
    for pattern in temp_patterns:
        for temp_file in glob.glob(pattern):
            try:
                os.remove(temp_file)
                count += 1
            except (OSError, PermissionError) as e:
                print(f"Не удалось удалить {temp_file}: {e}")
    
    if count > 0:
        print(f"Удалено {count} временных файлов")
    else:
        print("Временные файлы не найдены")

def main():
    # Создаем каталог для выходных файлов
    os.makedirs("output", exist_ok=True)
    
    try:
        # Извлекаем сегменты речи
        model = WhisperModel(MODEL_SIZE)
        srt_segments = extract_segments(model, VIDEO_PATH)
        
        # Открываем видео для получения длительности
        video = VideoFileClip(VIDEO_PATH)
        
        try:
            # Находим точки разбивки
            clip_ranges = find_split_points(srt_segments, video.duration, CLIP_COUNT)
            
            # Создаем клипы
            print("[5/5] Нарезаем видео и субтитры по смысловым частям...")
            
            for i, (clip_start, clip_end) in enumerate(clip_ranges):
                clip_index = i + 1
                print(f"Создаем клип {clip_index}/{CLIP_COUNT}... ({clip_start:.1f}s - {clip_end:.1f}s)")
                
                # Пути к файлам
                clip_path = f"output/clip_{clip_index}.mp4"
                output_path = f"output/clip_{clip_index}_subtitled.mp4"
                
                # Создаем клип
                if create_clip(video, clip_start, clip_end, clip_path):
                    # Создаем субтитры
                    srt_path = create_subtitles(srt_segments, clip_start, clip_end, clip_index)
                    
                    # Встраиваем субтитры
                    embed_subtitles(clip_path, srt_path, output_path)
        finally:
            # Закрываем видеофайл
            video.close()
        
        print(f"\n✅ Готово: {CLIP_COUNT} клипа с субтитрами в папке output")
    finally:
        # Удаляем временные файлы в любом случае
        cleanup_temp_files()

if __name__ == "__main__":
    main()
