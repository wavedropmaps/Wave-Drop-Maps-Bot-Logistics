import discord
from typing import Optional, Tuple

class DynamicHandlers:
    """
    Handles dynamic routing and localization for the Wave Drop Maps server.
    """
    
    # Target Roles
    TWITTER_ROLE_ID = 1210764292719247400
    INVITE_ROLE_ID = 1210764376676634684
    
    # Language Roles
    LANG_ROLES = {
        'EN': 1306357759163498496,
        'ES': 1483009690291146824,
        'FR': 1483009683156369468,
        'DE': 1483009677137547415,
        'IT': 1483009694447702096,
        'JP': 1489886259030524025,
        'PL': 1483009687283826768
    }

    # Maps (Language, TargetMethod) -> (Channel_ID, Message_Format)
    # TargetMethod: 'Invite' or 'Twitter'
    GARBAGE_MAPPINGS = {
        # English
        ('EN', 'Twitter'): (1210762955982110751, "{mention}, you provided an invalid image or didn't follow instructions. Please read <#{channel_id}> to learn how to get access."),
        ('EN', 'Invite'):  (1210762637995155456, "{mention}, you provided an invalid image or didn't follow instructions. Please read <#{channel_id}> to learn how to get access."),
        
        # Spanish
        ('ES', 'Invite'):  (1483015560664059955, "{mention}, proporcionaste una imagen inválida o no seguiste las instrucciones. Por favor, lee <#{channel_id}> para aprender cómo obtener acceso."),
        ('ES', 'Twitter'): (1483015582390423612, "{mention}, proporcionaste una imagen inválida o no seguiste las instrucciones. Por favor, lee <#{channel_id}> para aprender cómo obtener acceso."),
        
        # French
        ('FR', 'Invite'):  (1483015636283150336, "{mention}, vous avez fourni une image invalide ou n'avez pas suivi les instructions. Veuillez lire <#{channel_id}> pour savoir comment obtenir l'accès."),
        ('FR', 'Twitter'): (1483015655325159494, "{mention}, vous avez fourni une image invalide ou n'avez pas suivi les instructions. Veuillez lire <#{channel_id}> pour savoir comment obtenir l'accès."),
        
        # German
        ('DE', 'Invite'):  (1483015707733250089, "{mention}, du hast ein ungültiges Bild bereitgestellt oder die Anweisungen nicht befolgt. Bitte lies <#{channel_id}>, um zu erfahren, wie du Zugang erhältst."),
        ('DE', 'Twitter'): (1483015729119744031, "{mention}, du hast ein ungültiges Bild bereitgestellt oder die Anweisungen nicht befolgt. Bitte lies <#{channel_id}>, um zu erfahren, wie du Zugang erhältst."),
        
        # Italian
        ('IT', 'Invite'):  (1483015764033392680, "{mention}, hai fornito un'immagine non valida o non hai seguito le istruzioni. Leggi <#{channel_id}> per sapere come ottenere l'accesso."),
        ('IT', 'Twitter'): (1483015783301881886, "{mention}, hai fornito un'immagine non valida o non hai seguito le istruzioni. Leggi <#{channel_id}> per sapere come ottenere l'accesso."),
        
        # Polish
        ('PL', 'Invite'):  (1483015842886438993, "{mention}, podałeś nieprawidłowy obraz lub nie postępowałeś zgodnie z instrukcjami. Przeczytaj <#{channel_id}>, aby dowiedzieć się, jak uzyskać dostęp."),
        ('PL', 'Twitter'): (1483015865489412157, "{mention}, podałeś nieprawidłowy obraz lub nie postępowałeś zgodnie z instrukcjami. Przeczytaj <#{channel_id}>, aby dowiedzieć się, jak uzyskać dostęp."),
        
        # Japanese
        ('JP', 'Invite'):  (1489886478954795008, "{mention}、無効な画像を提供したか、指示に従いませんでした。アクセス方法については<#{channel_id}>をお読みください。"),
        ('JP', 'Twitter'): (1489886510344847391, "{mention}、無効な画像を提供したか、指示に従いませんでした。アクセス方法については<#{channel_id}>をお読みください。"),
    }

    @classmethod
    def get_dynamic_garbage_reply(cls, member: discord.Member) -> Optional[str]:
        """
        Determines the correct language and target method for the member.
        Returns the formatted string to send.
        """
        target_method = 'Twitter' # Default fallback
        if any(r.id == cls.INVITE_ROLE_ID for r in member.roles):
            target_method = 'Invite'
            
        user_lang = 'EN' # Default fallback
        for lang, role_id in cls.LANG_ROLES.items():
            if any(r.id == role_id for r in member.roles):
                user_lang = lang
                break
                
        mapping = cls.GARBAGE_MAPPINGS.get((user_lang, target_method))
        if not mapping:
            # Fallback to English Twitter if somehow lost
            mapping = cls.GARBAGE_MAPPINGS[('EN', 'Twitter')]
            
        channel_id, fmt_str = mapping
        return fmt_str.format(mention=member.mention, channel_id=channel_id)

    @staticmethod
    def get_invite_rejection(guild: discord.Guild, config: dict, member: discord.Member) -> str:
        """
        Finds the support role dynamically by name and constructs the ping string.
        """
        role_name = config.get("invite_support_role_name", "Support")
        role = discord.utils.get(guild.roles, name=role_name)
        
        ping_str = role.mention if role else f"@{role_name}"
        
        return f"Hey {member.mention}! To get access via invites, please contact a {ping_str} member. They will manually verify your invites and grant you the role. Feel free to DM them your proof!"

    @staticmethod
    def get_loot_routes_garbage_reply(member: discord.Member) -> str:
        """
        Specific garbage router logic for Loot Routes.
        """
        target_channel = "1188089464757686322" # Default fallback
        
        if any(r.id == 1188092082540261486 for r in member.roles):
            target_channel = "1188089464757686322"
        elif any(r.id == 1188090112987377705 for r in member.roles):
            target_channel = "1188089932334501980"
            
        return f"{member.mention}, this is incorrect. Please read <#{target_channel}> to learn how to get access."
