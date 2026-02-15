"""
Context Packer — buduje minimalny prompt z budżetem tokenów.

Kolejność priorytetów:
1. SYSTEM_PROMPT (stały)
2. PREFERENCES (z LTM)
3. WORKING_STATE (z STM)
4. RELEVANT_FACTS (top-K)
5. LAST_TURNS (ostatnie 3 pary, bez bloków kodu)
6. USER_MESSAGE
"""


# Prosty estimator tokenów (4 znaki ≈ 1 token)
def estimate_tokens(text: str) -> int:
    """Szacuje liczbę tokenów w tekście."""
    return max(1, len(text) // 4)


def strip_code_blocks(text: str) -> str:
    """Usuwa duże bloki kodu z wiadomości (zostawia resztę)."""
    lines = text.split('\n')
    result = []
    in_code_block = False
    code_block_lines = 0
    
    for line in lines:
        if line.strip().startswith('```'):
            if in_code_block:
                # Koniec bloku
                if code_block_lines > 5:
                    result.append('[...kod pominięty...]')
                in_code_block = False
                code_block_lines = 0
            else:
                in_code_block = True
                code_block_lines = 0
            continue
        
        if in_code_block:
            code_block_lines += 1
            if code_block_lines <= 5:
                result.append(line)
        else:
            result.append(line)
    
    return '\n'.join(result)


def pack_context(system_prompt: str,
                 preferences: list[str],
                 working_state_str: str,
                 relevant_facts: list[str],
                 conversation_messages: list[dict],
                 user_message: str,
                 decrypt_fn=None,
                 user_id: int = None,
                 budget: int = 3000,
                 last_turns: int = 3) -> list[dict]:
    """
    Buduje listę messages w formacie ChatML z budżetem tokenów.
    
    Args:
        system_prompt: stały system prompt
        preferences: lista preferencji użytkownika (krótkie stringi)
        working_state_str: WORKING_STATE w formacie tokenowo-tanim
        relevant_facts: lista relevantnych faktów
        conversation_messages: pełna historia rozmowy (zaszyfrowana)
        user_message: aktualna wiadomość użytkownika (plaintext)
        decrypt_fn: funkcja deszyfrująca (decrypt_fn(text, user_id))
        user_id: ID użytkownika (do deszyfrowania)
        budget: maksymalny budżet tokenów (bez system prompt)
        last_turns: ile ostatnich par wiadomości wziąć
    
    Returns:
        Lista messages w formacie [{"role", "content"}, ...]
    """
    messages = []
    used_tokens = 0
    
    # 1. SYSTEM_PROMPT — zawsze, nie liczy się do budżetu
    messages.append({"role": "system", "content": system_prompt})
    
    # 2. PREFERENCES — krótkie, priorytetowe
    if preferences:
        pref_text = "PREF:\n" + "; ".join(preferences)
        pref_tokens = estimate_tokens(pref_text)
        if used_tokens + pref_tokens <= budget:
            messages.append({"role": "system", "content": pref_text})
            used_tokens += pref_tokens
    
    # 3. WORKING_STATE — zawsze jeśli istnieje
    if working_state_str and working_state_str.strip():
        ws_tokens = estimate_tokens(working_state_str)
        if used_tokens + ws_tokens <= budget:
            messages.append({"role": "system", "content": working_state_str})
            used_tokens += ws_tokens
    
    # 4. RELEVANT_FACTS — top-K w budżecie
    if relevant_facts:
        facts_text = "FACTS:\n" + "\n".join(f"- {f}" for f in relevant_facts)
        facts_tokens = estimate_tokens(facts_text)
        
        if used_tokens + facts_tokens <= budget:
            messages.append({"role": "system", "content": facts_text})
            used_tokens += facts_tokens
        else:
            # Przycinaj fakty aby zmieścić się w budżecie
            remaining = budget - used_tokens - 20  # margines
            trimmed_facts = []
            for fact in relevant_facts:
                fact_line = f"- {fact}"
                if estimate_tokens("\n".join(trimmed_facts + [fact_line])) <= remaining:
                    trimmed_facts.append(fact_line)
                else:
                    break
            if trimmed_facts:
                facts_text = "FACTS:\n" + "\n".join(trimmed_facts)
                messages.append({"role": "system", "content": facts_text})
                used_tokens += estimate_tokens(facts_text)
    
    # 5. LAST_TURNS — ostatnie N par, bez bloków kodu
    if conversation_messages:
        # Bierz ostatnie last_turns*2 wiadomości (pary user/assistant)
        recent = conversation_messages[-(last_turns * 2):]
        
        turn_messages = []
        for msg in recent:
            content = msg.get('content', '')
            
            # Odszyfruj jeśli trzeba
            if decrypt_fn and user_id:
                content = decrypt_fn(content, user_id)
            
            # Usuń duże bloki kodu
            content = strip_code_blocks(content)
            
            # Przytnij zbyt długie wiadomości
            if len(content) > 800:
                content = content[:800] + "\n[...skrócono...]"
            
            turn_tokens = estimate_tokens(content)
            if used_tokens + turn_tokens <= budget:
                turn_messages.append({"role": msg['role'], "content": content})
                used_tokens += turn_tokens
            else:
                break
        
        messages.extend(turn_messages)
    
    # 6. USER_MESSAGE — zawsze, nie liczy się do budżetu
    messages.append({"role": "user", "content": user_message})
    
    return messages
